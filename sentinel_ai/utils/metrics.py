"""
Sentinel-AI Performance Metrics Collector.

Tracks TCR, Autonomy Score, Tool Selection Accuracy, MTTR-A,
SLA Compliance Rate, and Audit Completeness with rolling windows.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class MetricPoint:
    """Single metric data point."""
    timestamp: float
    value: float
    labels: dict = field(default_factory=dict)


class RollingWindow:
    """Time-based rolling window for metric aggregation."""

    def __init__(self, window_seconds: int = 3600):
        self.window_seconds = window_seconds
        self._points: deque[MetricPoint] = deque()

    def add(self, value: float, labels: Optional[dict] = None):
        now = time.time()
        self._points.append(MetricPoint(timestamp=now, value=value, labels=labels or {}))
        self._prune()

    def _prune(self):
        cutoff = time.time() - self.window_seconds
        while self._points and self._points[0].timestamp < cutoff:
            self._points.popleft()

    def count(self) -> int:
        self._prune()
        return len(self._points)

    def sum(self) -> float:
        self._prune()
        return sum(p.value for p in self._points)

    def avg(self) -> float:
        self._prune()
        if not self._points:
            return 0.0
        return self.sum() / len(self._points)

    def rate(self) -> float:
        """Compute success rate (values are 1.0 for success, 0.0 for failure)."""
        self._prune()
        if not self._points:
            return 0.0
        successes = sum(1 for p in self._points if p.value >= 1.0)
        return successes / len(self._points)

    def values(self) -> list[float]:
        self._prune()
        return [p.value for p in self._points]


class MetricsCollector:
    """
    Central metrics collection for Sentinel-AI.
    
    Tracks all KPIs defined in the architecture spec:
    - Task Completion Rate (TCR)
    - Autonomy Score (% without human help)
    - Tool Selection Accuracy
    - MTTR-A (Mean Time To Recovery - Automated)
    - SLA Compliance Rate
    - Audit Completeness
    """

    def __init__(self, window_minutes: int = 60):
        window_seconds = window_minutes * 60

        # Core metrics
        self.task_completions = RollingWindow(window_seconds)
        self.task_attempts = RollingWindow(window_seconds)
        self.human_escalations = RollingWindow(window_seconds)
        self.total_decisions = RollingWindow(window_seconds)
        self.tool_selections = RollingWindow(window_seconds)
        self.recovery_times = RollingWindow(window_seconds)
        self.sla_outcomes = RollingWindow(window_seconds)
        self.audit_entries = RollingWindow(window_seconds)
        self.expected_audits = RollingWindow(window_seconds)

        # Agent-specific metrics
        self.agent_execution_times: dict[str, RollingWindow] = {}
        self.agent_success_rates: dict[str, RollingWindow] = {}

        # Workflow-specific metrics  
        self.workflow_durations: dict[str, RollingWindow] = {}

        self._window_seconds = window_seconds

    # -----------------------------------------------------------------------
    # Recording Events
    # -----------------------------------------------------------------------

    def record_task_completion(self, success: bool):
        self.task_attempts.add(1.0)
        self.task_completions.add(1.0 if success else 0.0)

    def record_human_escalation(self):
        self.human_escalations.add(1.0)
        self.total_decisions.add(1.0)

    def record_autonomous_decision(self):
        self.total_decisions.add(1.0)

    def record_tool_selection(self, correct: bool):
        self.tool_selections.add(1.0 if correct else 0.0)

    def record_recovery(self, duration_seconds: float):
        self.recovery_times.add(duration_seconds)

    def record_sla_outcome(self, met: bool):
        self.sla_outcomes.add(1.0 if met else 0.0)

    def record_audit_entry(self):
        self.audit_entries.add(1.0)

    def record_expected_audit(self):
        self.expected_audits.add(1.0)

    def record_agent_execution(self, agent_name: str, duration: float, success: bool):
        if agent_name not in self.agent_execution_times:
            self.agent_execution_times[agent_name] = RollingWindow(self._window_seconds)
            self.agent_success_rates[agent_name] = RollingWindow(self._window_seconds)
        self.agent_execution_times[agent_name].add(duration)
        self.agent_success_rates[agent_name].add(1.0 if success else 0.0)

    def record_workflow_duration(self, workflow_type: str, duration: float):
        if workflow_type not in self.workflow_durations:
            self.workflow_durations[workflow_type] = RollingWindow(self._window_seconds)
        self.workflow_durations[workflow_type].add(duration)

    # -----------------------------------------------------------------------
    # Computed KPIs
    # -----------------------------------------------------------------------

    @property
    def task_completion_rate(self) -> float:
        """TCR: Percentage of tasks completed successfully."""
        return self.task_completions.rate() * 100

    @property
    def autonomy_score(self) -> float:
        """Percentage of decisions made without human intervention."""
        total = self.total_decisions.count()
        if total == 0:
            return 100.0
        escalations = self.human_escalations.count()
        return ((total - escalations) / total) * 100

    @property
    def tool_selection_accuracy(self) -> float:
        """Accuracy of tool/agent selection decisions."""
        return self.tool_selections.rate() * 100

    @property
    def mttr_a(self) -> float:
        """Mean Time To Recovery - Automated (seconds)."""
        return self.recovery_times.avg()

    @property
    def sla_compliance_rate(self) -> float:
        """Percentage of workflows completing within SLA."""
        return self.sla_outcomes.rate() * 100

    @property
    def audit_completeness(self) -> float:
        """Percentage of expected audit entries that were recorded."""
        expected = self.expected_audits.count()
        if expected == 0:
            return 100.0
        recorded = self.audit_entries.count()
        return min((recorded / expected) * 100, 100.0)

    def get_dashboard_snapshot(self) -> dict:
        """Get all KPIs as a dictionary for the monitoring dashboard."""
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kpis": {
                "task_completion_rate": round(self.task_completion_rate, 1),
                "autonomy_score": round(self.autonomy_score, 1),
                "tool_selection_accuracy": round(self.tool_selection_accuracy, 1),
                "mttr_a_seconds": round(self.mttr_a, 2),
                "sla_compliance_rate": round(self.sla_compliance_rate, 1),
                "audit_completeness": round(self.audit_completeness, 1),
            },
            "counts": {
                "total_tasks_attempted": self.task_attempts.count(),
                "total_decisions": self.total_decisions.count(),
                "human_escalations": self.human_escalations.count(),
            },
            "agent_performance": {},
            "workflow_performance": {},
        }

        for agent_name, times in self.agent_execution_times.items():
            snapshot["agent_performance"][agent_name] = {
                "avg_execution_time": round(times.avg(), 3),
                "success_rate": round(
                    self.agent_success_rates.get(agent_name, RollingWindow()).rate() * 100, 1
                ),
                "total_executions": times.count(),
            }

        for wf_type, durations in self.workflow_durations.items():
            snapshot["workflow_performance"][wf_type] = {
                "avg_duration_seconds": round(durations.avg(), 2),
                "total_completed": durations.count(),
            }

        return snapshot


# Global metrics singleton
_metrics: Optional[MetricsCollector] = None


def get_metrics(window_minutes: int = 60) -> MetricsCollector:
    """Get the global metrics collector singleton."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector(window_minutes)
    return _metrics
