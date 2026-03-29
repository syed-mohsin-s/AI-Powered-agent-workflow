"""
Sentinel-AI API Routes: Agents.

Agent health status and performance metrics endpoints.
"""

from fastapi import APIRouter

from sentinel_ai.models.schemas import AgentStatus, AgentListResponse
from sentinel_ai.utils.metrics import get_metrics

router = APIRouter(prefix="/api/agents", tags=["Agents"])

# Agent registry reference (populated at startup)
_agent_instances: dict = {}


def register_agents(agents: dict):
    global _agent_instances
    _agent_instances = agents


@router.get("/", response_model=AgentListResponse)
async def list_agents():
    """List all agents and their health status."""
    agents = []
    for name, agent in _agent_instances.items():
        health = agent.health_check()
        agents.append(AgentStatus(
            name=health.get("name", name),
            status=health.get("status", "unknown"),
            tasks_completed=health.get("tasks_completed", 0),
            tasks_failed=health.get("tasks_failed", 0),
            avg_response_time_ms=health.get("avg_response_time_ms", 0),
            circuit_breaker_open=False,
            last_heartbeat=health.get("last_heartbeat"),
        ))
    return AgentListResponse(agents=agents)


@router.get("/metrics")
async def get_agent_metrics():
    """Get performance metrics for all agents."""
    metrics = get_metrics()
    snapshot = metrics.get_dashboard_snapshot()
    return snapshot.get("agent_performance", {})
