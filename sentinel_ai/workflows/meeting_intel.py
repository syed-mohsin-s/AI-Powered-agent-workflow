"""
Sentinel-AI Meeting Intelligence Workflow.

Extract decisions → Identify tasks → Assign owners → Create tickets → Track → Remind → Escalate.
"""

import uuid
from sentinel_ai.models.workflow import TaskDefinition, WorkflowExecution


def create_meeting_workflow(input_data: dict, priority: int = 5) -> tuple[WorkflowExecution, list[TaskDefinition]]:
    """Build a Meeting Intelligence workflow with its task DAG."""
    workflow_id = str(uuid.uuid4())
    
    input_data.setdefault("type", "meeting_transcript")
    
    workflow = WorkflowExecution(
        id=workflow_id,
        workflow_type="meeting_intelligence",
        input_data=input_data,
        priority=priority,
    )

    tasks = [
        TaskDefinition(
            id=f"{workflow_id}_extract",
            name="Extract Decisions & Tasks",
            agent_type="intake",
            dependencies=[],
            input_data={"type": "meeting_transcript", "content": input_data.get("content", ""), **input_data},
            timeout_seconds=30,
        ),
        TaskDefinition(
            id=f"{workflow_id}_validate",
            name="Validate Extracted Data",
            agent_type="policy",
            dependencies=[f"{workflow_id}_extract"],
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_decide",
            name="Assign Owners & Priorities",
            agent_type="decision",
            dependencies=[f"{workflow_id}_validate"],
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_guard_ticketing",
            name="Reliability Guard: Ticketing & Export",
            agent_type="reliability_guard",
            dependencies=[f"{workflow_id}_decide"],
            input_data={"action": "create_issue", "target_system": "atlassian_mcp"},
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_create_tickets",
            name="Create Atlassian Tickets",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_ticketing"],
            input_data={"action": "create_issue", "target_system": "atlassian_mcp"},
            timeout_seconds=20,
        ),
        TaskDefinition(
            id=f"{workflow_id}_notify",
            name="Send Notifications",
            agent_type="execution",
            dependencies=[f"{workflow_id}_create_tickets"],
            input_data={"action": "send", "target_system": "email"},
            timeout_seconds=10,
        ),
        TaskDefinition(
            id=f"{workflow_id}_export",
            name="Generate Official Report",
            agent_type="execution",
            dependencies=[f"{workflow_id}_guard_ticketing"],
            input_data={"action": "write_report", "target_system": "local_system"},
            timeout_seconds=15,
        ),
        TaskDefinition(
            id=f"{workflow_id}_verify",
            name="Verify & Monitor",
            agent_type="verification",
            dependencies=[f"{workflow_id}_notify", f"{workflow_id}_export"],
            timeout_seconds=10,
        ),
    ]

    return workflow, tasks
