"""
Sentinel-AI Supervisor Agent (Heart).

Monitors agent health, handles retries, fallback routing,
timeout recovery, and prevents infinite loops and deadlocks.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.core.event_bus import Event, EventType, get_event_bus
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.supervisor")


class CircuitBreaker:
    """
    Circuit breaker pattern for agent failure protection.
    
    States:
    - CLOSED: Normal operation
    - OPEN: Agent is failing, redirect to fallback
    - HALF_OPEN: Testing if agent has recovered
    """

    def __init__(self, failure_threshold: int = 5, reset_timeout_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.reset_timeout = timedelta(seconds=reset_timeout_seconds)
        self.failure_count = 0
        self.state = "closed"
        self.last_failure_time: Optional[datetime] = None

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open" and self.last_failure_time:
            if datetime.now(timezone.utc) - self.last_failure_time > self.reset_timeout:
                self.state = "half_open"
                return True
        return self.state == "half_open"

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "threshold": self.failure_threshold,
        }


class SupervisorAgent(BaseAgent):
    """
    The Heart — monitors all running agents and ensures system health.
    
    Responsibilities:
    - Monitor agent health and tool execution
    - Handle retries, fallback routing, timeout recovery
    - Prevent infinite loops and deadlocks
    - Circuit breaker pattern for failing agents
    """

    def __init__(self):
        super().__init__(
            name="Supervisor Agent",
            agent_type="supervisor",
        )
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._agent_health: dict[str, dict] = {}
        self._event_bus = get_event_bus()
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None

    async def execute(self, context: dict) -> TaskResult:
        """Perform a supervision check."""
        action = context.get("input_data", {}).get("action", "health_check")

        if action == "health_check":
            return await self._perform_health_check(context)
        elif action == "circuit_check":
            return await self._check_circuits(context)
        else:
            return TaskResult(
                success=True,
                output_data={"status": "supervisor_active", "agents_monitored": len(self._agent_health)},
                confidence=1.0,
                reasoning="Supervisor is active and monitoring",
            )

    async def _perform_health_check(self, context: dict) -> TaskResult:
        """Check health of all registered agents."""
        health_report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agents": self._agent_health,
            "circuit_breakers": {
                name: cb.to_dict() for name, cb in self._circuit_breakers.items()
            },
            "issues": [],
        }

        # Detect issues
        for agent_name, health in self._agent_health.items():
            if health.get("status") == "failed":
                health_report["issues"].append(f"Agent {agent_name} is in FAILED state")
            cb = self._circuit_breakers.get(agent_name)
            if cb and cb.state == "open":
                health_report["issues"].append(f"Circuit breaker OPEN for {agent_name}")

        return TaskResult(
            success=True,
            output_data=health_report,
            confidence=0.95,
            reasoning=f"Health check complete. {len(health_report['issues'])} issues found.",
        )

    async def _check_circuits(self, context: dict) -> TaskResult:
        """Check all circuit breakers."""
        open_circuits = {
            name: cb.to_dict()
            for name, cb in self._circuit_breakers.items()
            if cb.state != "closed"
        }

        return TaskResult(
            success=True,
            output_data={"open_circuits": open_circuits, "total": len(self._circuit_breakers)},
            confidence=1.0,
            reasoning=f"{len(open_circuits)} circuit breakers not in closed state",
        )

    def get_circuit_breaker(self, agent_type: str) -> CircuitBreaker:
        """Get or create a circuit breaker for an agent type."""
        if agent_type not in self._circuit_breakers:
            self._circuit_breakers[agent_type] = CircuitBreaker()
        return self._circuit_breakers[agent_type]

    def record_agent_success(self, agent_type: str) -> None:
        """Record successful agent execution."""
        cb = self.get_circuit_breaker(agent_type)
        cb.record_success()

    def record_agent_failure(self, agent_type: str) -> None:
        """Record failed agent execution."""
        cb = self.get_circuit_breaker(agent_type)
        cb.record_failure()

        if cb.state == "open":
            logger.warning(f"Circuit breaker OPENED for agent: {agent_type}")
            asyncio.create_task(self._event_bus.publish(Event(
                event_type=EventType.AGENT_CIRCUIT_OPEN,
                data={"agent_type": agent_type, **cb.to_dict()},
                source="supervisor",
            )))

    def update_agent_health(self, agent_type: str, health: dict) -> None:
        """Update stored health info for an agent."""
        self._agent_health[agent_type] = health

    def can_dispatch_to(self, agent_type: str) -> bool:
        """Check if an agent is available (circuit breaker closed/half-open)."""
        cb = self._circuit_breakers.get(agent_type)
        if cb is None:
            return True
        return cb.can_execute()

    async def start_monitoring(self, interval_seconds: int = 5) -> None:
        """Start background health monitoring."""
        if self._monitoring:
            return
        self._monitoring = True
        self._monitor_task = asyncio.create_task(self._monitor_loop(interval_seconds))

    async def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self, interval: int) -> None:
        """Background monitoring loop."""
        while self._monitoring:
            try:
                await self._event_bus.publish(Event(
                    event_type=EventType.AGENT_HEALTH_CHECK,
                    data={
                        "agents": len(self._agent_health),
                        "open_circuits": sum(
                            1 for cb in self._circuit_breakers.values()
                            if cb.state == "open"
                        ),
                    },
                    source="supervisor",
                ))
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                await asyncio.sleep(interval)
