"""
Sentinel-AI Contract Lifecycle Management Workflow.

Draft Review → Legal Review → Financial Review → Negotiate → Approve → Sign → Store.
"""

import uuid
from sentinel_ai.models.workflow import TaskDefinition, WorkflowExecution


def create_contract_workflow(input_data: dict, priority: int = 5) -> tuple[WorkflowExecution, list[TaskDefinition]]:
    """Build a Contract Lifecycle Management workflow with its task DAG."""
    workflow_id = str(uuid.uuid4())
    
    input_data.setdefault("type", "contract")
    
    workflow = WorkflowExecution(
        id=workflow_id, workflow_type="contract_clm", input_data=input_data, priority=priority,
    )

    tasks = [
        TaskDefinition(
            id=f"{workflow_id}_intake",
            name="Extract Contract Data",
            agent_type="intake",
            dependencies=[],
            input_data={"type": "contract", **input_data},
            timeout_seconds=30,
        ),
        # Parallel legal + financial review
        TaskDefinition(
            id=f"{workflow_id}_legal",
            name="Legal Review",
            agent_type="policy",
            dependencies=[f"{workflow_id}_intake"],
            input_data={"review_type": "legal"},
            timeout_seconds=30,
        ),
        TaskDefinition(
            id=f"{workflow_id}_financial",
            name="Financial Risk Review",
            agent_type="policy",
            dependencies=[f"{workflow_id}_intake"],
            input_data={"review_type": "financial"},
            timeout_seconds=25,
        ),
        TaskDefinition(
            id=f"{workflow_id}_decide",
            name="Synthesize Reviews & Decide",
            agent_type="decision",
            dependencies=[f"{workflow_id}_legal", f"{workflow_id}_financial"],
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_guard_execute",
            name="Reliability Guard: Contract Execution",
            agent_type="reliability_guard",
            dependencies=[f"{workflow_id}_decide"],
            input_data={"action": "update_record", "target_system": "erp"},
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_execute",
            name="Execute Contract Signing",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_execute"],
            input_data={"action": "update_record", "target_system": "erp"},
            timeout_seconds=20,
        ),
        TaskDefinition(
            id=f"{workflow_id}_notify",
            name="Send Confirmation",
            agent_type="execution",
            dependencies=[f"{workflow_id}_execute"],
            input_data={"action": "send", "target_system": "email"},
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_verify",
            name="Verify & Archive",
            agent_type="verification",
            dependencies=[f"{workflow_id}_notify"],
            timeout_seconds=10,
        ),
    ]

    return workflow, tasks
