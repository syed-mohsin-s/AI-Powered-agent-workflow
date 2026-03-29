"""
Sentinel-AI Audit Model.

Agent Decision Record (AgDR) creation, hash chain management, and audit queries.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from sentinel_ai.utils.crypto import HashChain, compute_record_fingerprint
from sentinel_ai.utils.logger import get_logger

logger = get_logger("audit")

# Global hash chain instance
_hash_chain = HashChain()


class AgentDecisionRecord(BaseModel):
    """
    Agent Decision Record — the atomic unit of the audit trail.
    
    Every significant agent decision MUST produce one of these.
    Format matches the architecture spec exactly.
    """
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent: str
    trigger_event: str
    context: str
    decision: str
    reasoning: str
    alternatives: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    action_taken: str
    prior_state: str = ""
    resulting_state: str = ""
    status: str = "executed"  # executed | failed | escalated
    
    # Additional mandatory fields per spec
    why: str = ""           # WHY this decision was made
    trade_offs: str = ""    # WHAT trade-offs were accepted
    
    # Hash chain fields (populated by create_audit_record)
    record_hash: str = ""
    previous_hash: str = ""
    chain_timestamp: str = ""

    def to_spec_format(self) -> dict:
        """Export in the exact JSON format specified in the architecture."""
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "agent": self.agent,
            "trigger_event": self.trigger_event,
            "context": self.context,
            "decision": self.decision,
            "reasoning": f"{self.reasoning}. WHY: {self.why}",
            "alternatives": self.alternatives,
            "confidence": self.confidence,
            "action_taken": self.action_taken,
            "prior_state": self.prior_state,
            "resulting_state": self.resulting_state,
            "status": self.status,
            "trade_offs": self.trade_offs,
        }

    def to_chain_record(self) -> dict:
        """Format for hash chain verification."""
        return {
            "record_hash": self.record_hash,
            "previous_hash": self.previous_hash,
            "chain_timestamp": self.chain_timestamp,
            "record_data": self.to_spec_format(),
        }


def create_audit_record(
    agent: str,
    trigger_event: str,
    context: str,
    decision: str,
    reasoning: str,
    confidence: float,
    action_taken: str,
    alternatives: str = "",
    prior_state: str = "",
    resulting_state: str = "",
    status: str = "executed",
    why: str = "",
    trade_offs: str = "",
) -> AgentDecisionRecord:
    """
    Create a new AgDR and add it to the hash chain.
    
    This is the primary way agents should generate audit records.
    The hash chain is automatically maintained.
    """
    global _hash_chain

    record = AgentDecisionRecord(
        agent=agent,
        trigger_event=trigger_event,
        context=context,
        decision=decision,
        reasoning=reasoning,
        confidence=confidence,
        action_taken=action_taken,
        alternatives=alternatives,
        prior_state=prior_state,
        resulting_state=resulting_state,
        status=status,
        why=why,
        trade_offs=trade_offs,
    )

    # Chain the record
    record.previous_hash = _hash_chain.get_last_hash()
    record.chain_timestamp = datetime.now(timezone.utc).isoformat()
    record.record_hash = _hash_chain.add_record(record.to_spec_format())

    logger.audit_entry(record.decision_id, agent, decision, confidence)

    return record


async def persist_audit_record(record: AgentDecisionRecord, session) -> None:
    """Persist an AgDR to the database."""
    from sentinel_ai.models.database import AuditRecord as DBAuditRecord

    db_record = DBAuditRecord(
        decision_id=record.decision_id,
        workflow_id=None,  # Set by caller if workflow context exists
        timestamp=datetime.fromisoformat(record.timestamp),
        agent=record.agent,
        trigger_event=record.trigger_event,
        context=record.context,
        decision=record.decision,
        reasoning=record.reasoning,
        alternatives=record.alternatives,
        confidence=record.confidence,
        action_taken=record.action_taken,
        prior_state=record.prior_state,
        resulting_state=record.resulting_state,
        status=record.status,
        trade_offs=record.trade_offs,
        record_hash=record.record_hash,
        previous_hash=record.previous_hash,
        chain_timestamp=record.chain_timestamp,
    )
    session.add(db_record)
    await session.commit()
    logger.info(f"Audit record persisted: {record.decision_id}")


def verify_audit_chain(records: list[AgentDecisionRecord]) -> dict:
    """Verify the integrity of stored audit records."""
    chain_records = [r.to_chain_record() for r in records]
    return HashChain.verify_chain(chain_records)
