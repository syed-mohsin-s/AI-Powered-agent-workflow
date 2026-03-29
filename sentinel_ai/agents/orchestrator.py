"""
Sentinel-AI Orchestrator Agent (Brain).

Owns the lifecycle of every request. Converts inputs into task DAGs,
determines execution strategies, and resolves conflicts.
"""

import json
from typing import Any

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.orchestrator")


class OrchestratorAgent(BaseAgent):
    """
    The Brain — top-level agent that plans and coordinates workflows.
    
    Responsibilities:
    - Convert input into task graph (DAG)
    - Decide parallel vs sequential execution
    - Agent assignment for each task
    - Escalation strategy
    - Conflict resolution using confidence-weighted reasoning
    """

    def __init__(self):
        super().__init__(
            name="Orchestrator Agent",
            agent_type="orchestrator",
        )

    async def execute(self, context: dict) -> TaskResult:
        """Plan and structure a workflow execution."""
        workflow_type = context.get("workflow_type", "")
        input_data = context.get("input_data", {})

        # Use LLM for intelligent planning if available
        if self._llm.is_available:
            plan = await self._llm_plan(workflow_type, input_data)
        else:
            plan = self._rule_based_plan(workflow_type, input_data)

        return TaskResult(
            success=True,
            output_data={
                "plan": plan,
                "workflow_type": workflow_type,
                "strategy": "parallel_where_possible",
            },
            confidence=0.92,
            reasoning=f"Generated execution plan for {workflow_type} workflow with {len(plan.get('tasks', []))} tasks",
        )

    async def _llm_plan(self, workflow_type: str, input_data: dict) -> dict:
        """Use LLM for intelligent workflow planning."""
        prompt = f"""Plan an execution strategy for a {workflow_type} workflow.

Input data: {json.dumps(input_data, indent=2, default=str)[:2000]}

Return a JSON plan with:
- tasks: list of task names and their dependencies
- parallel_groups: which tasks can run in parallel
- risk_assessment: any potential issues
- estimated_duration_minutes: estimated completion time

Return ONLY valid JSON."""

        response = await self.llm_analyze(prompt)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return self._rule_based_plan(workflow_type, input_data)

    def _rule_based_plan(self, workflow_type: str, input_data: dict) -> dict:
        """Rule-based workflow planning fallback."""
        plans = {
            "p2p": {
                "tasks": [
                    "extract_invoice_data",
                    "validate_compliance",
                    "match_purchase_order",
                    "resolve_discrepancies",
                    "execute_payment",
                    "update_erp",
                    "generate_audit",
                ],
                "parallel_groups": [
                    ["validate_compliance", "match_purchase_order"],
                ],
                "risk_assessment": "Standard P2P flow",
                "estimated_duration_minutes": 15,
            },
            "meeting_intelligence": {
                "tasks": [
                    "extract_decisions",
                    "identify_tasks",
                    "assign_owners",
                    "create_tickets",
                    "setup_tracking",
                    "schedule_reminders",
                ],
                "parallel_groups": [
                    ["extract_decisions", "identify_tasks"],
                    ["create_tickets", "setup_tracking"],
                ],
                "risk_assessment": "Standard meeting intel flow",
                "estimated_duration_minutes": 10,
            },
            "onboarding": {
                "tasks": [
                    "validate_request",
                    "provision_accounts",
                    "setup_equipment",
                    "schedule_orientation",
                    "assign_buddy",
                    "verify_setup",
                ],
                "parallel_groups": [
                    ["provision_accounts", "setup_equipment", "assign_buddy"],
                ],
                "risk_assessment": "Standard onboarding flow",
                "estimated_duration_minutes": 60,
            },
            "contract_clm": {
                "tasks": [
                    "draft_review",
                    "legal_review",
                    "financial_review",
                    "negotiate_terms",
                    "obtain_approval",
                    "execute_signing",
                    "store_contract",
                ],
                "parallel_groups": [
                    ["legal_review", "financial_review"],
                ],
                "risk_assessment": "Standard CLM flow",
                "estimated_duration_minutes": 120,
            },
        }
        return plans.get(workflow_type, {
            "tasks": ["process_input", "validate", "execute", "verify"],
            "parallel_groups": [],
            "risk_assessment": "Generic workflow",
            "estimated_duration_minutes": 30,
        })

    async def resolve_conflict(
        self, results: list[dict], strategy: str = "confidence_weighted"
    ) -> dict:
        """
        Resolve conflicts between agent outputs using confidence weighting.
        
        Strategy:
        - confidence_weighted: Choose the result with highest confidence
        - policy_priority: compliance > SOP > convenience
        - consensus: Require majority agreement
        """
        if not results:
            return {}

        if strategy == "confidence_weighted":
            return max(results, key=lambda r: r.get("confidence", 0))
        elif strategy == "policy_priority":
            priority = {"compliance": 3, "sop": 2, "convenience": 1}
            return max(
                results,
                key=lambda r: priority.get(r.get("category", "convenience"), 0),
            )
        else:
            return results[0]
