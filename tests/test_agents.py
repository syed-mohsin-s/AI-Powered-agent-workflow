"""Tests for specialized agents."""

import pytest
from sentinel_ai.agents.intake import IntakeAgent
from sentinel_ai.agents.policy import PolicyAgent
from sentinel_ai.agents.decision import DecisionAgent
from sentinel_ai.agents.execution import ExecutionAgent
from sentinel_ai.agents.verification import VerificationAgent
from sentinel_ai.agents.recovery import RecoveryAgent
from sentinel_ai.agents.reliability_guard import ReliabilityGuardAgent


class TestIntakeAgent:
    @pytest.mark.asyncio
    async def test_invoice_extraction(self):
        agent = IntakeAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "extract",
            "input_data": {
                "type": "invoice",
                "vendor_name": "Acme Corp",
                "invoice_number": "INV-001",
                "total_amount": 5000,
                "po_number": "PO-123",
            },
            "shared_context": {},
            "attempt": 1,
        })
        assert result.success
        assert "extracted" in result.output_data
        assert result.output_data["extracted"]["vendor_name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_meeting_extraction(self):
        agent = IntakeAgent()
        result = await agent.execute({
            "workflow_type": "meeting_intelligence",
            "task_name": "extract",
            "input_data": {
                "type": "meeting_transcript",
                "content": "We decided to launch by April.\nAction: Bob will do the review.",
                "attendees": ["Alice", "Bob"],
            },
            "shared_context": {},
            "attempt": 1,
        })
        assert result.success
        extracted = result.output_data["extracted"]
        assert len(extracted["decisions"]) > 0

    @pytest.mark.asyncio
    async def test_empty_input(self):
        agent = IntakeAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "extract",
            "input_data": {},
            "shared_context": {},
            "attempt": 1,
        })
        # Empty input with no content returns failure
        assert not result.success


class TestPolicyAgent:
    @pytest.mark.asyncio
    async def test_p2p_approval(self):
        agent = PolicyAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "validate",
            "input_data": {"total_amount": 5000, "po_number": "PO-123", "vendor_name": "Acme"},
            "shared_context": {},
            "attempt": 1,
        })
        assert result.success
        assert result.output_data["approved"]

    @pytest.mark.asyncio
    async def test_p2p_over_limit(self):
        agent = PolicyAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "validate",
            "input_data": {"total_amount": 100000, "po_number": "PO-123", "vendor_name": "Acme"},
            "shared_context": {},
            "attempt": 1,
        })
        assert result.success
        assert not result.output_data["approved"]


class TestDecisionAgent:
    @pytest.mark.asyncio
    async def test_merge_outputs(self):
        agent = DecisionAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "decide",
            "input_data": {},
            "shared_context": {
                "extract": {"extracted": {"vendor": "Acme", "amount": 5000}},
                "validate": {"approved": True, "results": []},
            },
            "attempt": 1,
        })
        assert result.success
        assert result.output_data["decision"] == "approve"

    @pytest.mark.asyncio
    async def test_compliance_rejection(self):
        agent = DecisionAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "decide",
            "input_data": {},
            "shared_context": {
                "validate": {"approved": False, "failed_rules": ["amount_limit"]},
            },
            "attempt": 1,
        })
        assert result.success
        assert result.output_data["decision"] == "reject"


class TestVerificationAgent:
    @pytest.mark.asyncio
    async def test_verify_success(self):
        agent = VerificationAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "verify",
            "input_data": {},
            "shared_context": {
                "extract": {"extracted": {"vendor": "Acme"}},
                "validate": {"approved": True, "results": []},
                "execute": {"execution_result": {"status": "completed"}},
            },
            "attempt": 1,
        })
        assert result.success
        assert result.output_data["verified"]


class TestRecoveryAgent:
    @pytest.mark.asyncio
    async def test_level1_recovery(self):
        agent = RecoveryAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "recover",
            "input_data": {
                "failed_task": {
                    "id": "task_1",
                    "name": "Extract Data",
                    "agent": "intake",
                    "error": "timeout error",
                    "attempts": 1,
                }
            },
            "shared_context": {},
            "attempt": 1,
        })
        assert result.success
        assert result.output_data["recovery_level"] == 1

    @pytest.mark.asyncio
    async def test_level3_escalation(self):
        agent = RecoveryAgent()
        result = await agent.execute({
            "workflow_type": "p2p",
            "task_name": "recover",
            "input_data": {
                "failed_task": {
                    "name": "Critical Task",
                    "error": "persistent failure",
                    "attempts": 5,
                }
            },
            "shared_context": {},
            "attempt": 1,
        })
        assert not result.success  # Escalation = failure at this level
        assert result.output_data["recovery_level"] == 3


class TestReliabilityGuardAgent:
    @pytest.mark.asyncio
    async def test_passes_with_required_metadata(self):
        agent = ReliabilityGuardAgent()
        result = await agent.execute({
            "workflow_id": "wf-1",
            "task_id": "guard-1",
            "input_data": {"action": "create_issue", "target_system": "atlassian_mcp"},
            "shared_context": {},
        })
        assert result.success
        assert result.output_data["guard_passed"]

    @pytest.mark.asyncio
    async def test_fails_without_action_or_target(self):
        agent = ReliabilityGuardAgent()
        result = await agent.execute({
            "workflow_id": "wf-1",
            "task_id": "guard-1",
            "input_data": {},
            "shared_context": {},
        })
        assert not result.success

    @pytest.mark.asyncio
    async def test_blocks_duplicate_idempotency_key(self):
        agent = ReliabilityGuardAgent()
        shared_context = {}

        first = await agent.execute({
            "workflow_id": "wf-1",
            "task_id": "guard-1",
            "input_data": {
                "action": "create_issue",
                "target_system": "atlassian_mcp",
                "idempotency_key": "dup-key",
            },
            "shared_context": shared_context,
        })

        second = await agent.execute({
            "workflow_id": "wf-1",
            "task_id": "guard-2",
            "input_data": {
                "action": "create_issue",
                "target_system": "atlassian_mcp",
                "idempotency_key": "dup-key",
            },
            "shared_context": shared_context,
        })

        assert first.success
        assert not second.success

    @pytest.mark.asyncio
    async def test_blocks_invalid_action_target_pair(self):
        agent = ReliabilityGuardAgent()
        result = await agent.execute({
            "workflow_id": "wf-1",
            "task_id": "guard-3",
            "input_data": {
                "action": "create_payment",
                "target_system": "email",
            },
            "shared_context": {},
        })

        assert not result.success


class TestExecutionAgent:
    @pytest.mark.asyncio
    async def test_blocks_when_guard_failed(self):
        agent = ExecutionAgent()
        result = await agent.execute({
            "input_data": {"action": "create_issue", "target_system": "atlassian_mcp"},
            "shared_context": {
                "guard": {"guard_passed": False, "blocked": True, "reason": "policy blocked"}
            },
        })
        assert not result.success
        assert result.output_data["action"] == "blocked"

    @pytest.mark.asyncio
    async def test_marks_adapter_failed_status_as_failure(self):
        class FailingAdapter:
            async def execute(self, action: str, data: dict) -> dict:
                return {"status": "failed", "error": "downstream error"}

        agent = ExecutionAgent()
        agent.register_integration("atlassian_mcp", FailingAdapter())

        result = await agent.execute({
            "input_data": {"action": "create_issue", "target_system": "atlassian_mcp"},
            "shared_context": {},
        })
        assert not result.success


class TestDecisionAndVerificationUpgrades:
    @pytest.mark.asyncio
    async def test_decision_escalates_on_conflicting_compliance(self):
        agent = DecisionAgent()
        result = await agent.execute({
            "shared_context": {
                "policy_1": {"approved": True},
                "policy_2": {"approved": False},
            },
            "input_data": {},
        })
        assert result.success
        assert result.output_data["decision"] in {"reject", "escalate"}

    @pytest.mark.asyncio
    async def test_verification_flags_guard_block(self):
        agent = VerificationAgent()
        result = await agent.execute({
            "shared_context": {
                "guard_task": {"guard_passed": False, "blocked": True, "reason": "blocked by policy"},
                "execution_task": {"execution_result": {"status": "failed", "error": "x"}},
            },
            "input_data": {},
        })
        assert result.success
        assert not result.output_data["verified"]
        assert result.output_data["execution_failures"] >= 1

    @pytest.mark.asyncio
    async def test_verification_accepts_sent_status(self):
        agent = VerificationAgent()
        result = await agent.execute({
            "shared_context": {
                "validate": {"approved": True},
                "notify": {"execution_result": {"status": "sent"}},
            },
            "input_data": {},
        })
        assert result.success
        assert result.output_data["verified"]

    @pytest.mark.asyncio
    async def test_verification_accepts_matched_status(self):
        agent = VerificationAgent()
        result = await agent.execute({
            "shared_context": {
                "validate": {"approved": True},
                "match_po": {"execution_result": {"status": "matched"}},
            },
            "input_data": {},
        })
        assert result.success
        assert result.output_data["verified"]

    @pytest.mark.asyncio
    async def test_verification_accepts_simulated_success_with_fallback_error(self):
        agent = VerificationAgent()
        result = await agent.execute({
            "shared_context": {
                "validate": {"approved": True},
                "ticket": {
                    "execution_result": {
                        "status": "success",
                        "simulated": True,
                        "fallback_error": "connection closed",
                    }
                },
            },
            "input_data": {},
        })
        assert result.success
        assert result.output_data["verified"]
