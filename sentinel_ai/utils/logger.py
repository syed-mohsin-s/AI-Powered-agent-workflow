"""
Sentinel-AI Structured Logger.

JSON-formatted structured logging with correlation IDs for distributed tracing.
"""

import logging
import json
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional


# Context variable for correlation ID (propagated across asyncio tasks)
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_workflow_id: ContextVar[str] = ContextVar("workflow_id", default="")
_agent_id: ContextVar[str] = ContextVar("agent_id", default="")


def set_correlation_id(cid: Optional[str] = None) -> str:
    """Set the correlation ID for the current async context."""
    cid = cid or str(uuid.uuid4())
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_workflow_context(workflow_id: str) -> None:
    _workflow_id.set(workflow_id)


def set_agent_context(agent_id: str) -> None:
    _agent_id.set(agent_id)


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": _correlation_id.get(""),
            "workflow_id": _workflow_id.get(""),
            "agent_id": _agent_id.get(""),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include extra fields if present
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data

        # Include exception info
        if isinstance(record.exc_info, tuple) and len(record.exc_info) == 3 and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        return json.dumps(log_entry, default=str)


class SentinelLogger:
    """
    Structured logger for Sentinel-AI.
    
    Provides context-aware JSON logging with correlation IDs,
    workflow tracking, and agent identification.
    """

    def __init__(self, name: str, level: str = "INFO"):
        self.logger = logging.getLogger(f"sentinel.{name}")
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))

        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(JSONFormatter())
            self.logger.addHandler(handler)
            self.logger.propagate = False

    def _log(self, level: int, msg: str, extra_data: Optional[dict] = None, **kwargs):
        exc_info = kwargs.get("exc_info")
        if exc_info is True:
            exc_info = sys.exc_info()
        elif exc_info is False:
            exc_info = None

        record = self.logger.makeRecord(
            self.logger.name,
            level,
            "(unknown)",
            0,
            msg,
            args=(),
            exc_info=exc_info,
        )
        if extra_data:
            record.extra_data = extra_data
        self.logger.handle(record)

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        self._log(logging.CRITICAL, msg, **kwargs)

    def agent_action(self, agent: str, action: str, details: dict):
        """Log an agent action with structured metadata."""
        self._log(
            logging.INFO,
            f"[{agent}] {action}",
            extra_data={"agent": agent, "action": action, **details},
        )

    def workflow_event(self, workflow_id: str, event: str, details: dict):
        """Log a workflow lifecycle event."""
        self._log(
            logging.INFO,
            f"[Workflow:{workflow_id}] {event}",
            extra_data={"workflow_id": workflow_id, "event": event, **details},
        )

    def audit_entry(self, decision_id: str, agent: str, decision: str, confidence: float):
        """Log an audit-relevant decision."""
        self._log(
            logging.INFO,
            f"[AUDIT] {agent} decided: {decision} (confidence: {confidence:.2f})",
            extra_data={
                "audit": True,
                "decision_id": decision_id,
                "agent": agent,
                "decision": decision,
                "confidence": confidence,
            },
        )


def get_logger(name: str, level: str = "INFO") -> SentinelLogger:
    """Create a named Sentinel logger."""
    return SentinelLogger(name, level)
