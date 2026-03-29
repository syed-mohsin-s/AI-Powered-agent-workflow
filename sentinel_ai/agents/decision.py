"""
Sentinel-AI Decision / Synthesis Agent.

Merges outputs from multiple agents, resolves conflicts using
confidence weighting and policy priority hierarchy.
"""

import json
from typing import Any

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.decision")


class DecisionAgent(BaseAgent):
    """
    Merges multi-agent outputs and resolves conflicts.
    
    Resolution strategies:
    - confidence_weighted: Highest confidence wins
    - policy_priority: compliance > SOP > convenience
    - consensus: Majority vote
    """

    def __init__(self):
        super().__init__(
            name="Decision / Synthesis Agent",
            agent_type="decision",
        )

    async def execute(self, context: dict) -> TaskResult:
        """Synthesize outputs from previous agents."""
        shared_context = context.get("shared_context", {})
        input_data = context.get("input_data", {})

        if not shared_context:
            return TaskResult(
                success=True,
                output_data={"decision": "proceed", "merged_data": input_data},
                confidence=0.7,
                reasoning="No prior agent outputs to merge — proceeding with input data",
            )

        # Collect all agent outputs
        agent_outputs = {}
        compliance_results = []
        confidence_signals = []
        for task_id, output in shared_context.items():
            if isinstance(output, dict):
                agent_outputs[task_id] = output
                if "approved" in output:
                    compliance_results.append(output)
                if isinstance(output.get("confidence"), (int, float)):
                    confidence_signals.append(float(output["confidence"]))

        compliance_result = next((r for r in compliance_results if not r.get("approved", True)), None)

        # Policy priority: compliance decisions override everything
        if compliance_result and not compliance_result.get("approved", True):
            return TaskResult(
                success=True,
                output_data={
                    "decision": "reject",
                    "reason": "Compliance check failed",
                    "compliance_result": compliance_result,
                    "merged_data": agent_outputs,
                },
                confidence=0.95,
                reasoning="Decision: REJECT — compliance check failed (priority: compliance > SOP)",
            )

        approved_values = {bool(r.get("approved")) for r in compliance_results}
        if len(approved_values) > 1:
            return TaskResult(
                success=True,
                output_data={
                    "decision": "escalate",
                    "reason": "Conflicting compliance results",
                    "compliance_results": compliance_results,
                    "merged_data": agent_outputs,
                },
                confidence=0.45,
                reasoning="Decision: ESCALATE — conflicting compliance approvals detected",
            )

        # Merge all extracted data
        merged = {}
        for output in agent_outputs.values():
            if isinstance(output, dict):
                extracted = output.get("extracted", output)
                if isinstance(extracted, dict):
                    merged.update(extracted)

        # Use LLM for complex synthesis if available
        if self._llm.is_available and len(agent_outputs) > 2:
            synthesis = await self._llm_synthesize(agent_outputs)
            if synthesis:
                merged.update(synthesis)

        confidence = sum(confidence_signals) / len(confidence_signals) if confidence_signals else 0.88
        decision = "approve" if confidence >= 0.55 else "escalate"

        return TaskResult(
            success=True,
            output_data={
                "decision": decision,
                "merged_data": merged,
                "sources": list(agent_outputs.keys()),
                "synthesis_method": "confidence_weighted",
            },
            confidence=confidence,
            reasoning=f"Synthesized outputs from {len(agent_outputs)} agents. Decision: {decision.upper()}.",
        )

    async def _llm_synthesize(self, outputs: dict) -> dict | None:
        """Use LLM to intelligently merge conflicting outputs."""
        try:
            prompt = f"""Synthesize the following agent outputs into a coherent decision:

{json.dumps(outputs, indent=2, default=str)[:3000]}

Resolve any conflicts using these priorities:
1. Compliance/legal requirements (highest priority)
2. Standard operating procedures
3. Cost optimization
4. Convenience (lowest priority)

Return JSON with: decision (approve/reject/escalate), confidence (0-1), reasoning, merged_data"""

            response = await self.llm_analyze(prompt)
            return json.loads(response)
        except Exception:
            return None
