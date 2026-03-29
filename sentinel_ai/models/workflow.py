"""
Sentinel-AI Workflow Models.

In-memory workflow and task representations used by the engine.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class WorkflowStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"
    ESCALATED = "escalated"


@dataclass
class TaskResult:
    """Result from an agent executing a task."""
    success: bool
    output_data: dict = field(default_factory=dict)
    confidence: float = 0.0
    error_message: Optional[str] = None
    reasoning: str = ""
    duration_seconds: float = 0.0


@dataclass
class TaskDefinition:
    """
    A single task node in a workflow DAG.
    
    Tasks have dependencies on other tasks, allowing the engine
    to determine parallel vs sequential execution.
    """
    id: str
    name: str
    agent_type: str
    dependencies: list[str] = field(default_factory=list)
    input_data: dict = field(default_factory=dict)
    priority: int = 5
    timeout_seconds: int = 30
    max_retries: int = 3
    
    # Runtime state
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[TaskResult] = None
    attempt_count: int = 0
    dag_depth: int = 0
    
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def can_run(self, completed_tasks: set[str]) -> bool:
        """Check if all dependencies are satisfied."""
        return all(dep in completed_tasks for dep in self.dependencies)


@dataclass
class WorkflowExecution:
    """
    Runtime representation of a workflow being executed.
    
    Contains the complete task graph, current state, and execution context.
    """
    id: str
    workflow_type: str
    status: WorkflowStatus = WorkflowStatus.CREATED
    tasks: dict[str, TaskDefinition] = field(default_factory=dict)
    input_data: dict = field(default_factory=dict)
    output_data: dict = field(default_factory=dict)
    priority: int = 5
    
    # SLA
    sla_deadline: Optional[datetime] = None
    sla_met: Optional[bool] = None
    
    # Context shared across agents
    shared_context: dict = field(default_factory=dict)
    
    # Error tracking
    error_message: Optional[str] = None
    escalation_reason: Optional[str] = None
    
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def get_ready_tasks(self) -> list[TaskDefinition]:
        """Get tasks whose dependencies are all completed."""
        completed = {
            tid for tid, task in self.tasks.items()
            if task.status == TaskStatus.SUCCESS
        }
        return [
            task for task in self.tasks.values()
            if task.status == TaskStatus.PENDING and task.can_run(completed)
        ]

    def is_complete(self) -> bool:
        """Check if all tasks have finished (success or skipped)."""
        return all(
            task.status in (TaskStatus.SUCCESS, TaskStatus.SKIPPED)
            for task in self.tasks.values()
        )

    def has_failures(self) -> bool:
        """Check if any task has permanently failed."""
        return any(
            task.status in (TaskStatus.FAILED, TaskStatus.ESCALATED)
            for task in self.tasks.values()
        )

    def get_summary(self) -> dict:
        """Get execution summary for reporting."""
        task_counts = {}
        for task in self.tasks.values():
            task_counts[task.status.value] = task_counts.get(task.status.value, 0) + 1
        
        return {
            "id": self.id,
            "type": self.workflow_type,
            "status": self.status.value,
            "task_summary": task_counts,
            "total_tasks": len(self.tasks),
            "sla_met": self.sla_met,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
