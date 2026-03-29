"""
Sentinel-AI State Machine.

Manages workflow and task state transitions with validation.
"""

from sentinel_ai.models.workflow import WorkflowStatus, TaskStatus
from sentinel_ai.utils.logger import get_logger

logger = get_logger("state")


# ---------------------------------------------------------------------------
# Valid State Transitions
# ---------------------------------------------------------------------------

WORKFLOW_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.CREATED: {WorkflowStatus.RUNNING, WorkflowStatus.FAILED},
    WorkflowStatus.RUNNING: {
        WorkflowStatus.PAUSED, WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED, WorkflowStatus.ESCALATED,
    },
    WorkflowStatus.PAUSED: {WorkflowStatus.RUNNING, WorkflowStatus.FAILED},
    WorkflowStatus.COMPLETED: set(),  # Terminal state
    WorkflowStatus.FAILED: {WorkflowStatus.RUNNING},  # Allow retry
    WorkflowStatus.ESCALATED: {WorkflowStatus.RUNNING, WorkflowStatus.FAILED},
}

TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.SKIPPED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.SKIPPED},
    TaskStatus.RUNNING: {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.RETRYING},
    TaskStatus.RETRYING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.ESCALATED},
    TaskStatus.SUCCESS: set(),  # Terminal
    TaskStatus.FAILED: {TaskStatus.RETRYING, TaskStatus.ESCALATED, TaskStatus.RUNNING},
    TaskStatus.SKIPPED: set(),  # Terminal
    TaskStatus.ESCALATED: {TaskStatus.RUNNING, TaskStatus.FAILED},
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


def validate_workflow_transition(current: WorkflowStatus, target: WorkflowStatus) -> bool:
    """Check if a workflow state transition is valid."""
    allowed = WORKFLOW_TRANSITIONS.get(current, set())
    return target in allowed


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Check if a task state transition is valid."""
    allowed = TASK_TRANSITIONS.get(current, set())
    return target in allowed


def transition_workflow(current: WorkflowStatus, target: WorkflowStatus) -> WorkflowStatus:
    """
    Perform a validated workflow state transition.
    
    Raises InvalidTransitionError if the transition is not allowed.
    """
    if not validate_workflow_transition(current, target):
        raise InvalidTransitionError(
            f"Invalid workflow transition: {current.value} → {target.value}. "
            f"Allowed: {[s.value for s in WORKFLOW_TRANSITIONS.get(current, set())]}"
        )
    logger.info(
        f"Workflow transition: {current.value} → {target.value}",
        extra_data={"from": current.value, "to": target.value},
    )
    return target


def transition_task(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    """
    Perform a validated task state transition.
    
    Raises InvalidTransitionError if the transition is not allowed.
    """
    if not validate_task_transition(current, target):
        raise InvalidTransitionError(
            f"Invalid task transition: {current.value} → {target.value}. "
            f"Allowed: {[s.value for s in TASK_TRANSITIONS.get(current, set())]}"
        )
    logger.info(
        f"Task transition: {current.value} → {target.value}",
        extra_data={"from": current.value, "to": target.value},
    )
    return target
