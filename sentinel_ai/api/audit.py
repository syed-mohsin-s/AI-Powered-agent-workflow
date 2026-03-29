"""
Sentinel-AI API Routes: Audit.

Audit trail query and hash chain verification endpoints.
"""

from fastapi import APIRouter, Query
from typing import Optional

from sentinel_ai.models.schemas import AuditVerificationResponse
from sentinel_ai.utils.logger import get_logger

router = APIRouter(prefix="/api/audit", tags=["Audit"])
logger = get_logger("api.audit")

# In-memory audit store (shared with audit model)
_audit_records: list = []


def add_audit_record(record):
    """Add an audit record to the in-memory store."""
    _audit_records.append(record)


@router.get("/")
async def query_audit(
    agent: Optional[str] = None,
    workflow_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
):
    """Query audit trail records."""
    records = _audit_records

    if agent:
        records = [r for r in records if getattr(r, "agent", "") == agent]
    if workflow_id:
        records = [r for r in records if getattr(r, "workflow_id", "") == workflow_id]
    if status:
        records = [r for r in records if getattr(r, "status", "") == status]

    paginated = records[offset:offset + limit]
    
    return {
        "total": len(records),
        "records": [
            r.to_spec_format() if hasattr(r, "to_spec_format") else r
            for r in paginated
        ],
    }


@router.get("/verify", response_model=AuditVerificationResponse)
async def verify_audit_chain():
    """Verify the integrity of the entire audit hash chain."""
    from sentinel_ai.models.audit import verify_audit_chain as verify_chain

    if not _audit_records:
        return AuditVerificationResponse(valid=True, total_records=0, verified_records=0)

    result = verify_chain(_audit_records)
    return AuditVerificationResponse(**result)


@router.get("/{workflow_id}")
async def get_workflow_audit(workflow_id: str):
    """Get all audit records for a specific workflow."""
    records = [r for r in _audit_records if getattr(r, "workflow_id", "") == workflow_id]
    return {
        "workflow_id": workflow_id,
        "total": len(records),
        "records": [
            r.to_spec_format() if hasattr(r, "to_spec_format") else r
            for r in records
        ],
    }
