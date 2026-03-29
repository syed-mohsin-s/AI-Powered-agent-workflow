"""
Sentinel-AI Procure-to-Payment (P2P) Workflow.

End-to-end invoice processing: Extract → Validate → Match PO → Resolve → Pay → Update ERP → Audit.
"""

import uuid
from sentinel_ai.models.workflow import TaskDefinition, WorkflowExecution


def create_p2p_workflow(input_data: dict, priority: int = 5) -> tuple[WorkflowExecution, list[TaskDefinition]]:
    """Build a P2P workflow with its task DAG."""
    workflow_id = str(uuid.uuid4())
    
    input_data.setdefault("type", "invoice")
    
    workflow = WorkflowExecution(
        id=workflow_id,
        workflow_type="p2p",
        input_data=input_data,
        priority=priority,
    )

    tasks = [
        TaskDefinition(
            id=f"{workflow_id}_extract",
            name="Extract Invoice Data",
            agent_type="intake",
            dependencies=[],
            input_data={"type": "invoice", "content": input_data.get("content", ""), **input_data},
            timeout_seconds=30,
        ),
        TaskDefinition(
            id=f"{workflow_id}_validate",
            name="Validate Compliance",
            agent_type="policy",
            dependencies=[f"{workflow_id}_extract"],
            timeout_seconds=20,
        ),
        TaskDefinition(
            id=f"{workflow_id}_match_po",
            name="Match Purchase Order",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_match_po"],
            input_data={"action": "match_po", "target_system": "erp"},
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_guard_match_po",
            name="Reliability Guard: Match PO",
            agent_type="reliability_guard",
            dependencies=[f"{workflow_id}_extract"],
            input_data={"action": "match_po", "target_system": "erp"},
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_decide",
            name="Resolve Discrepancies",
            agent_type="decision",
            dependencies=[f"{workflow_id}_validate", f"{workflow_id}_match_po"],
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_pay",
            name="Execute Payment",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_pay"],
            input_data={"action": "create_payment", "target_system": "erp"},
            timeout_seconds=20,
        ),
        TaskDefinition(
            id=f"{workflow_id}_guard_pay",
            name="Reliability Guard: Execute Payment",
            agent_type="reliability_guard",
            dependencies=[f"{workflow_id}_decide"],
            input_data={"action": "create_payment", "target_system": "erp"},
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_update_erp",
            name="Update ERP Records",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_update_erp"],
            input_data={"action": "update_record", "target_system": "erp"},
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_guard_update_erp",
            name="Reliability Guard: Update ERP",
            agent_type="reliability_guard",
            dependencies=[f"{workflow_id}_pay"],
            input_data={"action": "update_record", "target_system": "erp"},
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_verify",
            name="Verify & Audit",
            agent_type="verification",
            dependencies=[f"{workflow_id}_update_erp"],
            timeout_seconds=10,
        ),
    ]

    return workflow, tasks
