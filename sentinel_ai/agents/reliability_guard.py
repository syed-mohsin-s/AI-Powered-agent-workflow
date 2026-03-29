"""
Sentinel-AI Reliability Guard Agent.

Performs pre-execution readiness and idempotency checks to reduce
duplicate or unsafe external actions.
"""

import hashlib
import json

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.models.workflow import TaskResult


class ReliabilityGuardAgent(BaseAgent):
    """Pre-execution guard for reliability and duplicate suppression."""

    def __init__(self):
        super().__init__(
            name="Reliability Guard Agent",
            agent_type="reliability_guard",
        )
        self._allowed_actions = {
            "erp": {"match_po", "create_payment", "update_record", "provision_accounts"},
            "email": {"send"},
            "servicenow": {"create_request"},
            "atlassian_mcp": {"create_issue", "update_issue", "add_comment", "search", "get_issue", "transition"},
            "local_system": {"write_report"},
        }

    async def execute(self, context: dict) -> TaskResult:
        input_data = context.get("input_data", {})
        shared_context = context.get("shared_context", {})
        workflow_id = context.get("workflow_id", "")
        task_id = context.get("task_id", "")

        action = input_data.get("action", "")
        target_system = input_data.get("target_system", "")

        if not action or not target_system:
            return TaskResult(
                success=False,
                error_message="Reliability guard requires both 'action' and 'target_system'",
                confidence=0.0,
                reasoning="Missing required execution metadata for preflight validation",
            )

        if not self._is_allowed_action(target_system, action):
            return TaskResult(
                success=False,
                error_message=f"Action '{action}' is not allowed for target '{target_system}'",
                confidence=0.1,
                reasoning="Preflight policy blocked unsupported action-target combination",
                output_data={
                    "guard_passed": False,
                    "blocked": True,
                    "action": action,
                    "target_system": target_system,
                },
            )

        for output in shared_context.values():
            if isinstance(output, dict) and output.get("decision") == "reject":
                return TaskResult(
                    success=True,
                    output_data={
                        "guard_passed": True,
                        "execution_mode": "skip_expected",
                        "reason": "Decision output is reject; downstream execution is expected to skip",
                    },
                    confidence=1.0,
                    reasoning="Guard allows flow; execution agent handles reject-path skip safely",
                )

        idempotency_key = input_data.get("idempotency_key") or self._build_idempotency_key(
            workflow_id=workflow_id,
            action=action,
            target_system=target_system,
            input_data=input_data,
        )

        registry = shared_context.setdefault("_reliability_guard_registry", {})
        if idempotency_key in registry and registry[idempotency_key] != task_id:
            return TaskResult(
                success=False,
                error_message=f"Duplicate execution signature blocked: {idempotency_key}",
                confidence=0.1,
                reasoning="Idempotency registry already contains equivalent execution request",
                output_data={
                    "guard_passed": False,
                    "blocked": True,
                    "idempotency_key": idempotency_key,
                },
            )

        registry[idempotency_key] = task_id

        return TaskResult(
            success=True,
            output_data={
                "guard_passed": True,
                "execution_mode": "allowed",
                "idempotency_key": idempotency_key,
                "target_system": target_system,
                "action": action,
                "risk_level": self._classify_risk(action),
            },
            confidence=0.95,
            reasoning="Preflight checks and idempotency validation passed",
        )

    def _build_idempotency_key(
        self,
        workflow_id: str,
        action: str,
        target_system: str,
        input_data: dict,
    ) -> str:
        """Build deterministic execution signature for duplicate protection."""
        critical_fields = {
            "invoice_number": input_data.get("invoice_number"),
            "po_number": input_data.get("po_number"),
            "employee_name": input_data.get("employee_name"),
            "contract_id": input_data.get("contract_id"),
            "summary": input_data.get("summary"),
            "action": action,
            "target_system": target_system,
            "workflow_id": workflow_id,
        }

        payload = json.dumps(critical_fields, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _is_allowed_action(self, target_system: str, action: str) -> bool:
        """Validate action/target pair against the policy matrix."""
        allowed = self._allowed_actions.get(target_system)
        if not allowed:
            return True
        return action in allowed

    @staticmethod
    def _classify_risk(action: str) -> str:
        """Classify action operational risk level."""
        high_risk = {"create_payment", "update_record", "transition"}
        medium_risk = {"create_issue", "update_issue", "create_request", "provision_accounts"}

        if action in high_risk:
            return "high"
        if action in medium_risk:
            return "medium"
        return "low"
