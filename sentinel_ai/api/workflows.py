"""
Sentinel-AI API Routes: Workflows.

REST endpoints for workflow submission, status, and lifecycle management.
"""

from fastapi import APIRouter, HTTPException
from typing import Optional

from sentinel_ai.core.engine import get_engine
from sentinel_ai.core.scheduler import get_scheduler
from sentinel_ai.models.schemas import WorkflowSubmission, WorkflowResponse, WorkflowListResponse, TaskResponse
from sentinel_ai.utils.logger import get_logger

logger = get_logger("api.workflows")
router = APIRouter(prefix="/api/workflows", tags=["Workflows"])

# Workflow factory mapping
WORKFLOW_FACTORIES = {}

def _init_factories():
    global WORKFLOW_FACTORIES
    from sentinel_ai.workflows.p2p import create_p2p_workflow
    from sentinel_ai.workflows.meeting_intel import create_meeting_workflow
    from sentinel_ai.workflows.onboarding import create_onboarding_workflow
    from sentinel_ai.workflows.contract_clm import create_contract_workflow
    
    WORKFLOW_FACTORIES = {
        "p2p": (create_p2p_workflow, 60),
        "meeting_intelligence": (create_meeting_workflow, 30),
        "onboarding": (create_onboarding_workflow, 1440),
        "contract_clm": (create_contract_workflow, 4320),
    }


def _workflow_to_response(wf) -> WorkflowResponse:
    tasks = []
    for tid, task in wf.tasks.items():
        tasks.append(TaskResponse(
            id=task.id,
            task_name=task.name,
            agent_type=task.agent_type,
            status=task.status.value,
            confidence_score=task.result.confidence if task.result else None,
            attempt_count=task.attempt_count,
            dag_depth=task.dag_depth,
            error_message=task.result.error_message if task.result else None,
            started_at=task.started_at.isoformat() if task.started_at else None,
            completed_at=task.completed_at.isoformat() if task.completed_at else None,
        ))
    
    scheduler = get_scheduler()
    tracker = scheduler.get_tracker(wf.id)
    
    return WorkflowResponse(
        id=wf.id,
        workflow_type=wf.workflow_type,
        status=wf.status.value,
        priority=wf.priority,
        input_data=wf.input_data,
        output_data=wf.output_data or None,
        error_message=wf.error_message,
        sla_deadline=wf.sla_deadline.isoformat() if wf.sla_deadline else None,
        sla_met=wf.sla_met,
        created_at=wf.created_at.isoformat(),
        started_at=wf.started_at.isoformat() if wf.started_at else None,
        completed_at=wf.completed_at.isoformat() if wf.completed_at else None,
        tasks=tasks,
    )


@router.post("/", response_model=dict)
async def submit_workflow(submission: WorkflowSubmission):
    """Submit a new workflow for execution."""
    if not WORKFLOW_FACTORIES:
        _init_factories()
    
    factory_info = WORKFLOW_FACTORIES.get(submission.workflow_type)
    if not factory_info:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown workflow type: {submission.workflow_type}. "
                   f"Available: {list(WORKFLOW_FACTORIES.keys())}",
        )
    
    factory_fn, sla_minutes = factory_info
    workflow, tasks = factory_fn(submission.input_data, submission.priority)
    
    engine = get_engine()
    workflow_id = await engine.submit_workflow(workflow, tasks, sla_minutes=sla_minutes)
    
    return {
        "workflow_id": workflow_id,
        "status": "submitted",
        "type": submission.workflow_type,
        "tasks_count": len(tasks),
        "sla_minutes": sla_minutes,
    }


@router.get("/", response_model=WorkflowListResponse)
async def list_workflows(status: Optional[str] = None, limit: int = 50):
    """List all workflows with optional status filter."""
    engine = get_engine()
    workflows = list(engine.get_active_workflows().values())
    
    if status:
        workflows = [w for w in workflows if w.status.value == status]
    
    responses = [_workflow_to_response(w) for w in workflows[:limit]]
    return WorkflowListResponse(total=len(workflows), workflows=responses)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str):
    """Get workflow status and task details."""
    engine = get_engine()
    workflow = engine.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    return _workflow_to_response(workflow)


@router.post("/{workflow_id}/pause")
async def pause_workflow(workflow_id: str):
    """Pause a running workflow."""
    engine = get_engine()
    success = await engine.pause_workflow(workflow_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot pause workflow (not running)")
    return {"status": "paused", "workflow_id": workflow_id}


@router.post("/{workflow_id}/resume")
async def resume_workflow(workflow_id: str):
    """Resume a paused workflow."""
    engine = get_engine()
    success = await engine.resume_workflow(workflow_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot resume workflow (not paused)")
    return {"status": "resumed", "workflow_id": workflow_id}


@router.delete("/{workflow_id}")
async def cancel_workflow(workflow_id: str):
    """Cancel a workflow."""
    engine = get_engine()
    success = await engine.cancel_workflow(workflow_id)
    if not success:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"status": "cancelled", "workflow_id": workflow_id}
