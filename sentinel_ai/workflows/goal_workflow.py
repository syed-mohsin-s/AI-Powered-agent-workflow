"""
Sentinel-AI Goal-Driven Workflow.

Creates a workflow from a natural language goal rather than
a predefined template. The Planner Agent generates the DAG at runtime.
"""

import uuid

from sentinel_ai.models.workflow import TaskDefinition, WorkflowExecution


def create_goal_workflow(
    goal: str,
    constraints: dict = None,
    priority: int = 5,
) -> tuple[WorkflowExecution, list[TaskDefinition]]:
    """
    Create a goal-driven workflow.

    Instead of a predefined task DAG, this creates a single initial
    "plan" task that uses the Planner Agent to generate the full DAG
    at runtime. The engine then injects the generated tasks.

    Args:
        goal: Natural language description of the objective.
        constraints: Optional constraints (budget, time, required tools, etc.).
        priority: Workflow priority (1=highest, 10=lowest).

    Returns:
        (WorkflowExecution, [planning task])
    """
    workflow_id = str(uuid.uuid4())

    workflow = WorkflowExecution(
        id=workflow_id,
        workflow_type="goal",
        input_data={
            "goal": goal,
            "constraints": constraints or {},
        },
        priority=priority,
    )

    # The only initial task is the planning step.
    # The engine will inject the planner's generated tasks after this completes.
    tasks = [
        TaskDefinition(
            id=f"{workflow_id}_plan",
            name="Generate Execution Plan",
            agent_type="planner",
            dependencies=[],
            input_data={
                "goal": goal,
                "constraints": constraints or {},
            },
            timeout_seconds=60,
        ),
    ]

    return workflow, tasks
