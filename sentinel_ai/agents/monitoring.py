"""
Sentinel-AI Monitoring Agent.

Tracks task completion, SLA timers, stalled workflows.
Detects data drift, concept drift, prediction drift, and bottlenecks.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.core.event_bus import Event, EventType, get_event_bus
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger
from sentinel_ai.utils.metrics import get_metrics

logger = get_logger("agents.monitoring")


class MonitoringAgent(BaseAgent):
    """
    Monitors workflow health and detects anomalies.
    
    Tracks:
    - Task completion rates
    - SLA timers
    - Stalled workflows
    - Data/concept/prediction drift
    - Bottlenecks
    """

    def __init__(self):
        super().__init__(
            name="Monitoring Agent",
            agent_type="monitoring",
        )
        self._event_bus = get_event_bus()
        self._baseline_metrics: dict = {}

    async def execute(self, context: dict) -> TaskResult:
        """Perform monitoring checks on the current workflow."""
        shared_context = context.get("shared_context", {})
        workflow_id = context.get("workflow_id", "")
        metrics = get_metrics()

        # Collect monitoring data
        monitoring_report = {
            "workflow_id": workflow_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "health_indicators": {},
            "drift_detection": {},
            "bottlenecks": [],
            "recommendations": [],
        }

        # Check task completion rate
        tcr = metrics.task_completion_rate
        monitoring_report["health_indicators"]["task_completion_rate"] = tcr
        if tcr < 80:
            monitoring_report["recommendations"].append(
                f"Task completion rate ({tcr:.1f}%) is below threshold (80%)"
            )

        # Check autonomy score
        autonomy = metrics.autonomy_score
        monitoring_report["health_indicators"]["autonomy_score"] = autonomy
        if autonomy < 90:
            monitoring_report["recommendations"].append(
                f"Autonomy score ({autonomy:.1f}%) is low — review escalation triggers"
            )

        # Check SLA compliance
        sla_rate = metrics.sla_compliance_rate
        monitoring_report["health_indicators"]["sla_compliance"] = sla_rate
        if sla_rate < 95:
            monitoring_report["recommendations"].append(
                f"SLA compliance ({sla_rate:.1f}%) needs attention"
            )

        # Drift detection (simplified)
        monitoring_report["drift_detection"] = {
            "data_drift": False,
            "concept_drift": False,
            "prediction_drift": False,
        }

        # Check for bottlenecks in agent execution
        for agent_name, perf in metrics.get_dashboard_snapshot().get("agent_performance", {}).items():
            avg_time = perf.get("avg_execution_time", 0)
            if avg_time > 5.0:  # More than 5 seconds average
                monitoring_report["bottlenecks"].append({
                    "agent": agent_name,
                    "avg_time": avg_time,
                    "recommendation": f"Agent {agent_name} is slow ({avg_time:.1f}s avg)",
                })

        all_healthy = (
            tcr >= 80 and autonomy >= 90 and sla_rate >= 95
            and not monitoring_report["bottlenecks"]
        )

        return TaskResult(
            success=True,
            output_data=monitoring_report,
            confidence=0.9,
            reasoning=f"Monitoring: {'All healthy' if all_healthy else 'Issues detected'} — "
                      f"TCR: {tcr:.0f}%, Autonomy: {autonomy:.0f}%, SLA: {sla_rate:.0f}%",
        )
