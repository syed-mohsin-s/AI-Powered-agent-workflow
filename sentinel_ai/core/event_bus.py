"""
Sentinel-AI Async Event Bus.

In-process pub/sub system using asyncio for decoupled agent communication.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Optional


class EventType(str, Enum):
    """All event types in the system."""
    # Workflow events
    WORKFLOW_CREATED = "workflow.created"
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_PAUSED = "workflow.paused"
    WORKFLOW_RESUMED = "workflow.resumed"
    WORKFLOW_ESCALATED = "workflow.escalated"
    
    # Task events
    TASK_QUEUED = "task.queued"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_RETRYING = "task.retrying"
    TASK_SKIPPED = "task.skipped"
    
    # Agent events
    AGENT_HEALTH_CHECK = "agent.health_check"
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_CIRCUIT_OPEN = "agent.circuit_open"
    AGENT_CIRCUIT_CLOSE = "agent.circuit_close"
    
    # SLA events
    SLA_WARNING = "sla.warning"
    SLA_BREACH = "sla.breach"
    SLA_MET = "sla.met"
    
    # Monitoring events
    DRIFT_DETECTED = "monitoring.drift_detected"
    BOTTLENECK_DETECTED = "monitoring.bottleneck"
    STALL_DETECTED = "monitoring.stall"
    
    # Escalation
    ESCALATION_TRIGGERED = "escalation.triggered"
    ESCALATION_RESOLVED = "escalation.resolved"
    
    # Audit
    AUDIT_RECORD_CREATED = "audit.record_created"
    AUDIT_CHAIN_VERIFIED = "audit.chain_verified"
    
    # Metrics
    METRICS_SNAPSHOT = "metrics.snapshot"


@dataclass
class Event:
    """An event in the system."""
    event_type: EventType
    data: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = ""
    correlation_id: str = ""
    workflow_id: str = ""

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "timestamp": self.timestamp,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "workflow_id": self.workflow_id,
        }


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """
    Async pub/sub event bus for Sentinel-AI.
    
    Supports:
    - Topic-based subscription
    - Wildcard subscriptions (e.g., "workflow.*")
    - Event replay for recovery
    - Non-blocking dispatch
    """

    def __init__(self, max_history: int = 1000):
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._wildcard_subscribers: dict[str, list[EventHandler]] = {}
        self._history: list[Event] = []
        self._max_history = max_history
        self._ws_callbacks: list[Callable] = []  # WebSocket broadcast callbacks

    def subscribe(self, event_type: str | EventType, handler: EventHandler) -> None:
        """Subscribe to a specific event type or wildcard pattern."""
        key = event_type.value if isinstance(event_type, EventType) else event_type

        if "*" in key:
            # Wildcard subscription: "workflow.*" matches all workflow events
            prefix = key.replace(".*", "").replace("*", "")
            if prefix not in self._wildcard_subscribers:
                self._wildcard_subscribers[prefix] = []
            self._wildcard_subscribers[prefix].append(handler)
        else:
            if key not in self._subscribers:
                self._subscribers[key] = []
            self._subscribers[key].append(handler)

    def unsubscribe(self, event_type: str | EventType, handler: EventHandler) -> None:
        """Remove a subscription."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        
        if key in self._subscribers:
            self._subscribers[key] = [h for h in self._subscribers[key] if h != handler]
        for prefix, handlers in self._wildcard_subscribers.items():
            self._wildcard_subscribers[prefix] = [h for h in handlers if h != handler]

    async def publish(self, event: Event) -> None:
        """
        Publish an event to all matching subscribers.
        
        Non-blocking: handlers are dispatched as concurrent tasks.
        """
        event_key = event.event_type.value

        # Store in history
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Collect all matching handlers
        handlers: list[EventHandler] = []

        # Exact match
        if event_key in self._subscribers:
            handlers.extend(self._subscribers[event_key])

        # Wildcard match
        for prefix, prefix_handlers in self._wildcard_subscribers.items():
            if event_key.startswith(prefix):
                handlers.extend(prefix_handlers)

        # Dispatch all handlers concurrently
        if handlers:
            tasks = [asyncio.create_task(self._safe_dispatch(h, event)) for h in handlers]
            await asyncio.gather(*tasks, return_exceptions=True)

        # Notify WebSocket clients
        for ws_callback in self._ws_callbacks:
            try:
                await ws_callback(event.to_dict())
            except Exception:
                pass

    async def _safe_dispatch(self, handler: EventHandler, event: Event) -> None:
        """Dispatch to a handler with error protection."""
        try:
            await handler(event)
        except Exception as e:
            # Log but don't crash the event bus
            import traceback
            traceback.print_exc()

    def register_ws_callback(self, callback: Callable) -> None:
        """Register a WebSocket broadcast callback."""
        self._ws_callbacks.append(callback)

    def unregister_ws_callback(self, callback: Callable) -> None:
        """Remove a WebSocket broadcast callback."""
        self._ws_callbacks = [cb for cb in self._ws_callbacks if cb != callback]

    def get_history(
        self,
        event_type: Optional[str] = None,
        workflow_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get recent event history, optionally filtered."""
        events = self._history
        if event_type:
            events = [e for e in events if e.event_type.value == event_type]
        if workflow_id:
            events = [e for e in events if e.workflow_id == workflow_id]
        return [e.to_dict() for e in events[-limit:]]

    async def replay(
        self,
        from_index: int = 0,
        event_type: Optional[str] = None,
    ) -> int:
        """Replay events from history (for recovery)."""
        events = self._history[from_index:]
        if event_type:
            events = [e for e in events if e.event_type.value == event_type]
        
        for event in events:
            await self.publish(event)
        
        return len(events)


# Global event bus singleton
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus singleton."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
