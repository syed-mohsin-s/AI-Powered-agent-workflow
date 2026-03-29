"""
Sentinel-AI SLA-Aware Scheduler.

Priority scheduling with SLA tracking, deadline monitoring, and preemptive warnings.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sentinel_ai.core.event_bus import Event, EventType, get_event_bus
from sentinel_ai.models.workflow import WorkflowExecution, WorkflowStatus
from sentinel_ai.utils.logger import get_logger

logger = get_logger("scheduler")


class SLATracker:
    """Track SLA compliance for a single workflow."""

    def __init__(self, workflow_id: str, deadline: datetime, warning_threshold: float = 0.75):
        self.workflow_id = workflow_id
        self.deadline = deadline
        self.warning_threshold = warning_threshold
        self.warning_sent = False
        self.breach_sent = False
        self.created_at = datetime.now(timezone.utc)

    @property
    def time_remaining(self) -> timedelta:
        return self.deadline - datetime.now(timezone.utc)

    @property
    def time_elapsed(self) -> timedelta:
        return datetime.now(timezone.utc) - self.created_at

    @property
    def progress_ratio(self) -> float:
        """How much of the SLA window has been consumed (0.0 to 1.0+)."""
        total = (self.deadline - self.created_at).total_seconds()
        if total <= 0:
            return 1.0
        elapsed = self.time_elapsed.total_seconds()
        return elapsed / total

    @property
    def is_breached(self) -> bool:
        return datetime.now(timezone.utc) > self.deadline

    @property
    def should_warn(self) -> bool:
        return not self.warning_sent and self.progress_ratio >= self.warning_threshold

    def to_dict(self) -> dict:
        return {
            "workflow_id": self.workflow_id,
            "deadline": self.deadline.isoformat(),
            "time_remaining_seconds": max(0, self.time_remaining.total_seconds()),
            "progress_ratio": round(self.progress_ratio, 3),
            "is_breached": self.is_breached,
            "warning_sent": self.warning_sent,
        }


class Scheduler:
    """
    SLA-aware priority scheduler for workflow execution.
    
    Features:
    - SLA deadline tracking with preemptive warnings
    - Priority-based workflow ordering
    - Dynamic re-prioritization based on SLA proximity
    - Background monitoring loop
    """

    def __init__(self, check_interval_seconds: int = 10):
        self._trackers: dict[str, SLATracker] = {}
        self._check_interval = check_interval_seconds
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._event_bus = get_event_bus()

    def register_workflow(
        self,
        workflow: WorkflowExecution,
        sla_minutes: int,
        warning_threshold: float = 0.75,
    ) -> SLATracker:
        """Register a workflow for SLA tracking."""
        deadline = datetime.now(timezone.utc) + timedelta(minutes=sla_minutes)
        workflow.sla_deadline = deadline

        tracker = SLATracker(
            workflow_id=workflow.id,
            deadline=deadline,
            warning_threshold=warning_threshold,
        )
        self._trackers[workflow.id] = tracker

        logger.info(
            f"SLA registered: workflow {workflow.id}, deadline in {sla_minutes}m",
            extra_data={"workflow_id": workflow.id, "sla_minutes": sla_minutes},
        )
        return tracker

    def unregister_workflow(self, workflow_id: str) -> None:
        """Remove a workflow from SLA tracking."""
        self._trackers.pop(workflow_id, None)

    def get_tracker(self, workflow_id: str) -> Optional[SLATracker]:
        return self._trackers.get(workflow_id)

    def get_all_trackers(self) -> list[dict]:
        """Get all SLA tracker snapshots for the dashboard."""
        return [t.to_dict() for t in self._trackers.values()]

    def prioritize_workflows(self, workflows: list[WorkflowExecution]) -> list[WorkflowExecution]:
        """
        Sort workflows by urgency.
        
        Priority is determined by:
        1. SLA proximity (closer deadline = higher priority)
        2. Explicit priority field
        3. Creation time (FIFO for equal priority)
        """
        def priority_key(wf: WorkflowExecution):
            tracker = self._trackers.get(wf.id)
            sla_urgency = 0.0
            if tracker:
                # Invert remaining time: closer deadline = higher urgency
                remaining = tracker.time_remaining.total_seconds()
                sla_urgency = 1.0 / max(remaining, 1.0) * 10000
            
            return (
                -sla_urgency,          # Higher urgency first (negative for ascending sort)
                -wf.priority,          # Higher priority first
                wf.created_at,         # Earlier first
            )

        return sorted(workflows, key=priority_key)

    async def start_monitoring(self) -> None:
        """Start the background SLA monitoring loop."""
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("SLA monitoring started")

    async def stop_monitoring(self) -> None:
        """Stop the background monitoring loop."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("SLA monitoring stopped")

    async def _monitor_loop(self) -> None:
        """Background loop checking SLA status."""
        while self._running:
            try:
                await self._check_slas()
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SLA monitor error: {e}", exc_info=True)
                await asyncio.sleep(self._check_interval)

    async def _check_slas(self) -> None:
        """Check all tracked SLAs for warnings and breaches."""
        for workflow_id, tracker in list(self._trackers.items()):
            # Send warning if threshold reached
            if tracker.should_warn:
                tracker.warning_sent = True
                await self._event_bus.publish(Event(
                    event_type=EventType.SLA_WARNING,
                    workflow_id=workflow_id,
                    data={
                        "workflow_id": workflow_id,
                        "time_remaining_seconds": tracker.time_remaining.total_seconds(),
                        "progress_ratio": tracker.progress_ratio,
                        "deadline": tracker.deadline.isoformat(),
                    },
                    source="scheduler",
                ))
                logger.warning(
                    f"SLA Warning: workflow {workflow_id} at {tracker.progress_ratio:.0%} of SLA window",
                    extra_data=tracker.to_dict(),
                )

            # Send breach event
            if tracker.is_breached and not tracker.breach_sent:
                tracker.breach_sent = True
                await self._event_bus.publish(Event(
                    event_type=EventType.SLA_BREACH,
                    workflow_id=workflow_id,
                    data={
                        "workflow_id": workflow_id,
                        "deadline": tracker.deadline.isoformat(),
                        "overdue_seconds": abs(tracker.time_remaining.total_seconds()),
                    },
                    source="scheduler",
                ))
                logger.error(
                    f"SLA BREACH: workflow {workflow_id} exceeded deadline",
                    extra_data=tracker.to_dict(),
                )


# Global scheduler singleton
_scheduler: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
