"""
Sentinel-AI Employee Onboarding Workflow.

Validate → Provision Accounts → Setup Equipment → Schedule Orientation → Verify.
"""

import uuid
from sentinel_ai.models.workflow import TaskDefinition, WorkflowExecution


def create_onboarding_workflow(input_data: dict, priority: int = 5) -> tuple[WorkflowExecution, list[TaskDefinition]]:
    """Build an Employee Onboarding workflow with its task DAG."""
    workflow_id = str(uuid.uuid4())
    
    input_data.setdefault("type", "onboarding_request")
    
    workflow = WorkflowExecution(
        id=workflow_id, workflow_type="onboarding", input_data=input_data, priority=priority,
    )

    tasks = [
        TaskDefinition(
            id=f"{workflow_id}_intake",
            name="Process Onboarding Request",
            agent_type="intake",
            dependencies=[],
            input_data={"type": "onboarding_request", **input_data},
            timeout_seconds=20,
        ),
        TaskDefinition(
            id=f"{workflow_id}_validate",
            name="Validate Against HR Policies",
            agent_type="policy",
            dependencies=[f"{workflow_id}_intake"],
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_decide",
            name="Approve Onboarding",
            agent_type="decision",
            dependencies=[f"{workflow_id}_validate"],
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_guard_provisioning",
            name="Reliability Guard: Provisioning",
            agent_type="reliability_guard",
            dependencies=[f"{workflow_id}_decide"],
            input_data={"action": "provision_accounts", "target_system": "erp"},
            timeout_seconds=10,
        ),
        # Parallel group: provision + equipment + buddy
        TaskDefinition(
            id=f"{workflow_id}_provision",
            name="Provision IT Accounts",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_provisioning"],
            input_data={"action": "provision_accounts", "target_system": "erp"},
            timeout_seconds=20,
        ),
        TaskDefinition(
            id=f"{workflow_id}_equipment",
            name="Setup Equipment",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_provisioning"],
            input_data={"action": "create_request", "target_system": "servicenow"},
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_notify",
            name="Send Welcome Email & Schedule Orientation",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_provisioning"],
            input_data={"action": "send", "target_system": "email"},
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_verify",
            name="Verify Onboarding Setup",
            agent_type="verification",
            dependencies=[f"{workflow_id}_provision", f"{workflow_id}_equipment", f"{workflow_id}_notify"],
            timeout_seconds=15,
        ),
    ]

    return workflow, tasks
