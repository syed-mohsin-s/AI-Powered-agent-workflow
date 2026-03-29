"""
Sentinel-AI Database Models.

PostgreSQL + SQLAlchemy async ORM models for workflows, tasks, and audit records.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer,
    JSON, String, Text, Index, func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from sentinel_ai.config import get_config
from sentinel_ai.utils.logger import get_logger

logger = get_logger("database")


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


# ---------------------------------------------------------------------------
# Workflow Model
# ---------------------------------------------------------------------------

class WorkflowRecord(Base):
    """Persisted workflow execution record."""
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    input_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    output_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # SLA tracking
    sla_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_met: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    tasks: Mapped[list["TaskRecord"]] = relationship(back_populates="workflow", cascade="all, delete-orphan")
    audit_records: Mapped[list["AuditRecord"]] = relationship(back_populates="workflow", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_workflow_type_status", "workflow_type", "status"),
    )


# ---------------------------------------------------------------------------
# Task Model
# ---------------------------------------------------------------------------

class TaskRecord(Base):
    """Individual task within a workflow."""
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflows.id"), nullable=False, index=True)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    
    # Execution details
    input_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    output_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Retry tracking
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    
    # Dependencies (stored as JSON list of task IDs)
    dependencies: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    
    # Ordering within the DAG
    dag_depth: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    workflow: Mapped["WorkflowRecord"] = relationship(back_populates="tasks")

    __table_args__ = (
        Index("ix_task_workflow_status", "workflow_id", "status"),
    )


# ---------------------------------------------------------------------------
# Audit Record (Agent Decision Record - AgDR)
# ---------------------------------------------------------------------------

class AuditRecord(Base):
    """
    Cryptographically verifiable Agent Decision Record (AgDR).
    
    Each record contains a SHA-256 hash that chains to the previous record,
    creating a tamper-evident audit trail.
    """
    __tablename__ = "audit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    decision_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    workflow_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("workflows.id"), nullable=True, index=True)
    
    # AgDR fields (matching the spec exactly)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    agent: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trigger_event: Mapped[str] = mapped_column(String(256), nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    alternatives: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    action_taken: Mapped[str] = mapped_column(Text, nullable=False)
    prior_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resulting_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="executed")
    
    # Trade-offs accepted (mandatory per spec)
    trade_offs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Cryptographic hash chain
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
    
    # Relationships
    workflow: Mapped[Optional["WorkflowRecord"]] = relationship(back_populates="audit_records")

    __table_args__ = (
        Index("ix_audit_agent_timestamp", "agent", "timestamp"),
        Index("ix_audit_workflow_timestamp", "workflow_id", "timestamp"),
    )


# ---------------------------------------------------------------------------
# Agent Health Record
# ---------------------------------------------------------------------------

class AgentHealthRecord(Base):
    """Tracks agent health snapshots over time."""
    __tablename__ = "agent_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # healthy, degraded, failed
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tasks_completed: Mapped[int] = mapped_column(Integer, default=0)
    tasks_failed: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_time_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    circuit_breaker_open: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Database Engine & Session Factory
# ---------------------------------------------------------------------------

_engine = None
_session_factory = None


async def init_database(database_url: Optional[str] = None) -> None:
    """Initialize the async database engine and create all tables."""
    global _engine, _session_factory

    if database_url is None:
        config = get_config()
        database_url = config.database.url

    _engine = create_async_engine(
        database_url,
        echo=False,
        pool_size=10,
        max_overflow=20,
    )

    _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

    # Create all tables
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized", extra_data={"url": database_url.split("@")[-1]})


async def get_session() -> AsyncSession:
    """Get an async database session."""
    if _session_factory is None:
        await init_database()
    return _session_factory()


async def close_database() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database closed")
