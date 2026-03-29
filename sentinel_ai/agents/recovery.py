"""
Sentinel-AI Recovery / Critic Agent.

Handles failures using reflection loops, alternate tool/agent selection,
and multi-level recovery strategy.
"""

import json
from typing import Any

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.recovery")


class RecoveryAgent(BaseAgent):
    """
    Self-healing recovery agent.
    
    Recovery levels:
    - Level 1 (Local): Retry with corrected parameters
    - Level 2 (Orchestrator): Re-plan workflow, swap agent/tool
    - Level 3 (Human): Escalate when confidence < threshold
    """

    def __init__(self):
        super().__init__(
            name="Recovery / Critic Agent",
            agent_type="recovery",
        )
        self._recovery_history: list[dict] = []

    async def execute(self, context: dict) -> TaskResult:
        """Attempt to recover from a failure."""
        failed_task = context.get("input_data", {}).get("failed_task", context.get("failed_task", {}))
        shared_context = context.get("shared_context", {})

        if not failed_task:
            return TaskResult(
                success=True,
                output_data={"action": "no_recovery_needed"},
                confidence=1.0,
                reasoning="No failed task to recover from",
            )

        error = failed_task.get("error", "Unknown error")
        attempts = failed_task.get("attempts", 0)
        task_name = failed_task.get("name", "unknown")

        logger.info(f"Recovery agent analyzing failure: {task_name} — {error}")

        # Use LLM for intelligent recovery analysis
        if self._llm.is_available:
            recovery_plan = await self._llm_recovery(failed_task, shared_context)
            if recovery_plan:
                return recovery_plan

        # Rule-based recovery
        return await self._rule_based_recovery(failed_task, shared_context)

    async def _llm_recovery(self, failed_task: dict, context: dict) -> TaskResult | None:
        """Use LLM to analyze failure and suggest recovery."""
        try:
            prompt = f"""A task has failed in our enterprise workflow. Analyze and suggest recovery:

Failed Task: {json.dumps(failed_task, indent=2, default=str)}
Context: {json.dumps(context, indent=2, default=str)[:2000]}

Recovery levels:
1. Local: Fix parameters and retry
2. Orchestrator: Swap agent or re-plan
3. Human: Escalate (last resort)

Return JSON with: level (1-3), action, corrected_parameters (if level 1), reasoning, confidence (0-1)"""

            response = await self.llm_analyze(prompt)
            plan = json.loads(response)

            level = plan.get("level", 3)
            confidence = plan.get("confidence", 0.5)

            if level <= 2 and confidence > 0.5:
                return TaskResult(
                    success=True,
                    output_data={
                        "recovery_level": level,
                        "action": plan.get("action", "retry"),
                        "corrected_parameters": plan.get("corrected_parameters", {}),
                    },
                    confidence=confidence,
                    reasoning=f"Recovery Level {level}: {plan.get('reasoning', 'Automated recovery')}",
                )
            else:
                return TaskResult(
                    success=False,
                    output_data={
                        "recovery_level": 3,
                        "action": "escalate",
                        "escalation_reason": plan.get("reasoning", "Recovery confidence too low"),
                    },
                    confidence=confidence,
                    error_message="Escalation required — automated recovery insufficient",
                    reasoning=f"Escalating to human: {plan.get('reasoning', 'Low confidence')}",
                )
        except Exception:
            return None

    async def _rule_based_recovery(self, failed_task: dict, context: dict) -> TaskResult:
        """Rule-based recovery strategy."""
        error = failed_task.get("error", "").lower()
        attempts = failed_task.get("attempts", 0)

        # Level 1: Local recovery
        if attempts <= 1:
            if "timeout" in error:
                return TaskResult(
                    success=True,
                    output_data={
                        "recovery_level": 1,
                        "action": "retry_with_extended_timeout",
                        "corrected_parameters": {"timeout_seconds": 60},
                    },
                    confidence=0.7,
                    reasoning="Level 1 Recovery: Extending timeout and retrying",
                )
            elif "schema" in error or "validation" in error:
                return TaskResult(
                    success=True,
                    output_data={
                        "recovery_level": 1,
                        "action": "retry_with_relaxed_validation",
                        "corrected_parameters": {"strict_mode": False},
                    },
                    confidence=0.6,
                    reasoning="Level 1 Recovery: Relaxing validation and retrying",
                )
            else:
                return TaskResult(
                    success=True,
                    output_data={
                        "recovery_level": 1,
                        "action": "simple_retry",
                        "corrected_parameters": {},
                    },
                    confidence=0.5,
                    reasoning=f"Level 1 Recovery: Simple retry for error: {error[:100]}",
                )

        # Level 2: Orchestrator recovery
        if attempts <= 3:
            return TaskResult(
                success=True,
                output_data={
                    "recovery_level": 2,
                    "action": "re_plan",
                    "recommendation": "Skip or replace the failed task",
                },
                confidence=0.4,
                reasoning="Level 2 Recovery: Recommending workflow re-planning",
            )

        # Level 3: Human escalation
        return TaskResult(
            success=False,
            output_data={
                "recovery_level": 3,
                "action": "escalate",
                "escalation_reason": f"Task '{failed_task.get('name')}' failed {attempts} times: {error}",
            },
            confidence=0.2,
            error_message=f"Human escalation required for task: {failed_task.get('name')}",
            reasoning=f"Level 3: All automated recovery exhausted after {attempts} attempts. Escalating.",
        )
