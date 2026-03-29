"""
Sentinel-AI Policy / Compliance Agent.

Validates data against business rules, legal constraints, and regulatory requirements.
Returns approval/rejection with confidence score and reasoning.
"""

import json
from typing import Any

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.policy")


class PolicyAgent(BaseAgent):
    """
    Validates against business policies, legal constraints, and regulations.
    
    Output: approval / rejection + confidence score + reasoning.
    """

    def __init__(self):
        super().__init__(
            name="Policy / Compliance Agent",
            agent_type="policy",
        )
        # Built-in compliance rules
        self._rules = {
            "p2p": [
                {"name": "amount_limit", "check": self._check_amount_limit, "priority": "high"},
                {"name": "po_required", "check": self._check_po_required, "priority": "high"},
                {"name": "vendor_approved", "check": self._check_vendor_approved, "priority": "medium"},
                {"name": "budget_available", "check": self._check_budget, "priority": "high"},
                {"name": "duplicate_check", "check": self._check_duplicate, "priority": "medium"},
            ],
            "onboarding": [
                {"name": "role_approved", "check": self._check_role_approved, "priority": "high"},
                {"name": "background_check", "check": self._check_background, "priority": "high"},
                {"name": "equipment_policy", "check": self._check_equipment_policy, "priority": "low"},
            ],
            "contract_clm": [
                {"name": "legal_review", "check": self._check_legal_terms, "priority": "high"},
                {"name": "financial_risk", "check": self._check_financial_risk, "priority": "high"},
                {"name": "jurisdiction", "check": self._check_jurisdiction, "priority": "medium"},
            ],
        }

    async def execute(self, context: dict) -> TaskResult:
        """Validate data against applicable policies."""
        workflow_type = context.get("workflow_type", "")
        input_data = context.get("input_data", {})
        shared_context = context.get("shared_context", {})

        # Merge extracted data from intake
        data = {**input_data}
        for task_output in shared_context.values():
            if isinstance(task_output, dict) and "extracted" in task_output:
                data.update(task_output["extracted"])

        # Use LLM for complex compliance analysis if available
        if self._llm.is_available:
            llm_result = await self._llm_compliance_check(workflow_type, data)
            if llm_result:
                return llm_result

        # Rule-based validation
        rules = self._rules.get(workflow_type, [])
        results = []
        for rule in rules:
            result = await rule["check"](data)
            result["rule_name"] = rule["name"]
            result["priority"] = rule["priority"]
            results.append(result)

        # Aggregate results
        all_passed = all(r["passed"] for r in results)
        high_priority_failed = any(
            not r["passed"] and r["priority"] == "high" for r in results
        )
        confidence = sum(r.get("confidence", 0.8) for r in results) / max(len(results), 1)

        approved = all_passed or (not high_priority_failed and confidence > 0.7)

        return TaskResult(
            success=True,
            output_data={
                "approved": approved,
                "results": results,
                "failed_rules": [r for r in results if not r["passed"]],
                "passed_rules": [r for r in results if r["passed"]],
            },
            confidence=confidence,
            reasoning=f"Policy check: {'APPROVED' if approved else 'REJECTED'}. "
                      f"{sum(r['passed'] for r in results)}/{len(results)} rules passed.",
        )

    async def _llm_compliance_check(self, workflow_type: str, data: dict) -> TaskResult | None:
        """Use LLM for intelligent compliance analysis."""
        try:
            prompt = f"""Analyze the following {workflow_type} data for compliance:

{json.dumps(data, indent=2, default=str)[:3000]}

Check for:
1. Regulatory compliance
2. Policy adherence
3. Risk factors
4. Missing required fields

Return JSON with: approved (bool), confidence (0-1), issues (list), recommendations (list)"""

            response = await self.llm_analyze(prompt)
            result = json.loads(response)
            return TaskResult(
                success=True,
                output_data=result,
                confidence=result.get("confidence", 0.8),
                reasoning=f"LLM compliance analysis: {'Approved' if result.get('approved') else 'Rejected'}",
            )
        except Exception:
            return None

    # -----------------------------------------------------------------------
    # Built-in Rule Checks
    # -----------------------------------------------------------------------

    async def _check_amount_limit(self, data: dict) -> dict:
        total = data.get("total_amount", 0)
        if isinstance(total, str):
            try:
                total = float(total.replace(",", "").replace("$", ""))
            except ValueError:
                total = 0
        limit = 50000
        return {
            "passed": total <= limit,
            "confidence": 0.95,
            "message": f"Amount ${total:.2f} {'within' if total <= limit else 'exceeds'} limit of ${limit:.2f}",
        }

    async def _check_po_required(self, data: dict) -> dict:
        po = data.get("po_number", "")
        return {
            "passed": bool(po),
            "confidence": 0.9,
            "message": f"PO number {'present' if po else 'MISSING'}",
        }

    async def _check_vendor_approved(self, data: dict) -> dict:
        vendor = data.get("vendor_name", "")
        return {
            "passed": bool(vendor),
            "confidence": 0.7,
            "message": f"Vendor '{vendor}' {'found' if vendor else 'not specified'}",
        }

    async def _check_budget(self, data: dict) -> dict:
        return {
            "passed": True,
            "confidence": 0.8,
            "message": "Budget check passed (simulated)",
        }

    async def _check_duplicate(self, data: dict) -> dict:
        return {
            "passed": True,
            "confidence": 0.85,
            "message": "No duplicates detected (simulated)",
        }

    async def _check_role_approved(self, data: dict) -> dict:
        position = data.get("position", "")
        return {
            "passed": bool(position),
            "confidence": 0.9,
            "message": f"Position '{position}' {'approved' if position else 'not specified'}",
        }

    async def _check_background(self, data: dict) -> dict:
        return {
            "passed": True,
            "confidence": 0.85,
            "message": "Background check cleared (simulated)",
        }

    async def _check_equipment_policy(self, data: dict) -> dict:
        equipment = data.get("equipment_needed", [])
        return {
            "passed": len(equipment) <= 5,
            "confidence": 0.9,
            "message": f"Equipment request ({len(equipment)} items) within policy limits",
        }

    async def _check_legal_terms(self, data: dict) -> dict:
        return {
            "passed": True,
            "confidence": 0.7,
            "message": "Legal terms reviewed (simulated)",
        }

    async def _check_financial_risk(self, data: dict) -> dict:
        value = data.get("value", 0)
        return {
            "passed": value < 1000000,
            "confidence": 0.8,
            "message": f"Financial risk assessment: {'low' if value < 100000 else 'medium' if value < 1000000 else 'HIGH'}",
        }

    async def _check_jurisdiction(self, data: dict) -> dict:
        return {
            "passed": True,
            "confidence": 0.85,
            "message": "Jurisdiction check passed (simulated)",
        }
