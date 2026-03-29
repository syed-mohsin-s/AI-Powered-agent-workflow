"""
Sentinel-AI Pydantic Schemas.

Request/response models for the API layer.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Workflow Schemas
# ---------------------------------------------------------------------------

class WorkflowSubmission(BaseModel):
    """Request to create and execute a new workflow."""
    workflow_type: str = Field(..., description="Type: p2p, onboarding, meeting_intelligence, contract_clm")
    input_data: dict = Field(default_factory=dict, description="Workflow-specific input data")
    priority: int = Field(default=5, ge=1, le=10, description="Priority 1-10 (10 = highest)")
    metadata: Optional[dict] = Field(default=None, description="Additional metadata")


class WorkflowResponse(BaseModel):
    """Workflow status response."""
    id: str
    workflow_type: str
    status: str
    priority: int
    input_data: dict
    output_data: Optional[dict] = None
    error_message: Optional[str] = None
    sla_deadline: Optional[str] = None
    sla_met: Optional[bool] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    tasks: list["TaskResponse"] = []

    class Config:
        from_attributes = True


class WorkflowListResponse(BaseModel):
    """Paginated list of workflows."""
    total: int
    workflows: list[WorkflowResponse]


# ---------------------------------------------------------------------------
# Task Schemas
# ---------------------------------------------------------------------------

class TaskResponse(BaseModel):
    """Task status within a workflow."""
    id: str
    task_name: str
    agent_type: str
    status: str
    confidence_score: Optional[float] = None
    attempt_count: int = 0
    dag_depth: int = 0
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Agent Schemas
# ---------------------------------------------------------------------------

class AgentStatus(BaseModel):
    """Current status of an agent."""
    name: str
    status: str  # healthy, degraded, failed
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_response_time_ms: float = 0.0
    circuit_breaker_open: bool = False
    last_heartbeat: Optional[str] = None


class AgentListResponse(BaseModel):
    """List of all agents and their health."""
    agents: list[AgentStatus]


# ---------------------------------------------------------------------------
# Audit Schemas
# ---------------------------------------------------------------------------

class AuditRecordResponse(BaseModel):
    """Audit record (AgDR) response."""
    decision_id: str
    timestamp: str
    agent: str
    trigger_event: str
    context: str
    decision: str
    reasoning: str
    alternatives: str = ""
    confidence: float
    action_taken: str
    prior_state: str = ""
    resulting_state: str = ""
    status: str
    trade_offs: str = ""
    record_hash: str
    previous_hash: str

    class Config:
        from_attributes = True


class AuditVerificationResponse(BaseModel):
    """Result of audit chain verification."""
    valid: bool
    total_records: int
    verified_records: int
    first_invalid_index: Optional[int] = None
    first_invalid_reason: Optional[str] = None


class AuditQueryParams(BaseModel):
    """Query parameters for audit trail search."""
    agent: Optional[str] = None
    workflow_id: Optional[str] = None
    status: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    min_confidence: Optional[float] = None
    limit: int = Field(default=100, le=1000)
    offset: int = 0


# ---------------------------------------------------------------------------
# Metrics Schemas
# ---------------------------------------------------------------------------

class MetricsResponse(BaseModel):
    """Dashboard metrics snapshot."""
    timestamp: str
    kpis: dict
    counts: dict
    agent_performance: dict
    workflow_performance: dict


# ---------------------------------------------------------------------------
# WebSocket Event Schema
# ---------------------------------------------------------------------------

class WSEvent(BaseModel):
    """WebSocket event payload sent to dashboard clients."""
    event_type: str  # workflow_update, task_update, agent_health, sla_warning, metric_update
    timestamp: str
    data: dict


# Resolve forward references
WorkflowResponse.model_rebuild()
