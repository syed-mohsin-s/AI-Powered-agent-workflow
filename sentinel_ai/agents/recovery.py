"""
Sentinel-AI Recovery / Critic Agent.

Handles failures using reflection loops, alternate tool/agent selection,
and multi-level recovery strategy. Uses the ReflectionEngine for
intelligent failure analysis and the ToolRegistry for tool switching.
"""

import json

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.core.reflection import get_reflection_engine
from sentinel_ai.core.tool_registry import get_tool_registry
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.recovery")


class RecoveryAgent(BaseAgent):
    """
    Self-healing recovery agent with semantic failure analysis.

    Recovery levels:
    - Level 1 (Local): Retry with corrected parameters
    - Level 1.5 (Tool Switch): Try an alternative tool for the same goal
    - Level 2 (Orchestrator): Re-plan workflow, swap agent/tool
    - Level 3 (Human): Escalate when confidence < threshold
    """

    def __init__(self):
        super().__init__(
            name="Recovery / Critic Agent",
            agent_type="recovery",
        )
        self._recovery_history: list[dict] = []
        self._reflection_engine = get_reflection_engine()
        self._tool_registry = get_tool_registry()

    async def execute(self, context: dict) -> TaskResult:
        """Attempt to recover from a failure using reflection-guided strategies."""
        failed_task = context.get("input_data", {}).get(
            "failed_task", context.get("failed_task", {})
        )
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
        tool_used = failed_task.get("tool_used")

        logger.info(f"Recovery agent analyzing failure: {task_name} — {error}")

        # Step 1: Reflect on the failure using the ReflectionEngine
        available_alternatives = self._find_alternative_tools(
            task_name=task_name,
            tool_used=tool_used,
            error=error,
        )

        reflection = await self._reflection_engine.reflect_on_failure(
            task_name=task_name,
            error=error,
            tool_used=tool_used,
            available_alternatives=[t.name for t in available_alternatives],
            attempts=attempts,
            context=shared_context,
        )

        # Record the reflection
        workflow_id = context.get("workflow_id", "")
        if workflow_id:
            self._reflection_engine.record_reflection(workflow_id, reflection)

        # Store in recovery history
        self._recovery_history.append(
            {
                "task": task_name,
                "error": error,
                "reflection": reflection.to_dict(),
                "tool_used": tool_used,
            }
        )

        # Step 2: Choose recovery strategy based on reflection

        # Level 1.5: Tool switching (new capability from semantic recovery)
        if reflection.can_switch_tool and available_alternatives and attempts <= 2:
            best_alternative = available_alternatives[0]
            logger.info(
                f"Semantic recovery: switching from '{tool_used}' to '{best_alternative.name}' "
                f"for task '{task_name}'"
            )
            return TaskResult(
                success=True,
                output_data={
                    "recovery_level": 1.5,
                    "action": "switch_tool",
                    "previous_tool": tool_used,
                    "alternative_tool": best_alternative.name,
                    "alternative_provider": best_alternative.provider,
                    "reflection": reflection.to_dict(),
                    "root_cause": reflection.root_cause,
                },
                confidence=reflection.confidence,
                reasoning=f"Semantic Recovery: Switching to '{best_alternative.name}' — {reflection.analysis}",
                reasoning_trace=[
                    {
                        "step": "semantic_recovery",
                        "from_tool": tool_used,
                        "to_tool": best_alternative.name,
                        "root_cause": reflection.root_cause,
                        "analysis": reflection.analysis,
                    }
                ],
            )

        # Level 1: Local recovery (retry with corrections)
        if reflection.retry_recommended and attempts <= 1:
            # Use LLM for intelligent parameter correction if available
            if self._llm.is_available:
                recovery_plan = await self._llm_recovery(failed_task, shared_context)
                if recovery_plan:
                    return recovery_plan

            return await self._rule_based_local_recovery(failed_task, reflection)

        # Level 2: Orchestrator recovery (re-plan)
        if attempts <= 3:
            return TaskResult(
                success=True,
                output_data={
                    "recovery_level": 2,
                    "action": "re_plan",
                    "recommendation": "Skip or replace the failed task",
                    "reflection": reflection.to_dict(),
                    "root_cause": reflection.root_cause,
                    "suggestions": reflection.suggestions,
                },
                confidence=max(0.3, reflection.confidence - 0.2),
                reasoning=f"Level 2 Recovery: {reflection.analysis}. Recommending workflow re-planning.",
                reasoning_trace=[
                    {
                        "step": "orchestrator_recovery",
                        "root_cause": reflection.root_cause,
                        "suggestions": reflection.suggestions,
                    }
                ],
            )

        # Level 3: Human escalation
        return TaskResult(
            success=False,
            output_data={
                "recovery_level": 3,
                "action": "escalate",
                "escalation_reason": f"Task '{task_name}' failed {attempts} times: {error}",
                "reflection": reflection.to_dict(),
                "root_cause": reflection.root_cause,
                "recovery_history": self._recovery_history[-5:],  # Last 5 attempts
            },
            confidence=0.2,
            error_message=f"Human escalation required for task: {task_name}",
            reasoning=f"Level 3: All automated recovery exhausted after {attempts} attempts. "
            f"Root cause: {reflection.root_cause}. Escalating.",
            reasoning_trace=[
                {
                    "step": "escalation",
                    "attempts": attempts,
                    "root_cause": reflection.root_cause,
                    "analysis": reflection.analysis,
                }
            ],
        )

    def _find_alternative_tools(
        self,
        task_name: str,
        tool_used: str | None,
        error: str,
    ) -> list:
        """Find alternative tools that could handle the same action."""
        if not tool_used:
            return []

        # Extract the action from the tool name (e.g., "erp_create_payment" → "create_payment")
        action_parts = tool_used.split("_", 1)
        search_query = action_parts[1] if len(action_parts) > 1 else tool_used

        # Search for alternatives, excluding the failed tool
        alternatives = self._tool_registry.search_tools(
            query=search_query,
            exclude=[tool_used],
            min_reliability=0.3,
        )

        # Also try searching by task name
        if not alternatives:
            alternatives = self._tool_registry.search_tools(
                query=task_name,
                exclude=[tool_used],
                min_reliability=0.3,
            )

        # Rank by reliability
        return self._tool_registry.rank_tools(alternatives)[:3]

    async def _llm_recovery(
        self, failed_task: dict, context: dict
    ) -> TaskResult | None:
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
                        "escalation_reason": plan.get(
                            "reasoning", "Recovery confidence too low"
                        ),
                    },
                    confidence=confidence,
                    error_message="Escalation required — automated recovery insufficient",
                    reasoning=f"Escalating to human: {plan.get('reasoning', 'Low confidence')}",
                )
        except Exception:
            return None

    async def _rule_based_local_recovery(
        self, failed_task: dict, reflection
    ) -> TaskResult:
        """Rule-based local recovery guided by reflection."""
        error = failed_task.get("error", "").lower()

        if "timeout" in error:
            return TaskResult(
                success=True,
                output_data={
                    "recovery_level": 1,
                    "action": "retry_with_extended_timeout",
                    "corrected_parameters": {"timeout_seconds": 60},
                    "root_cause": reflection.root_cause,
                },
                confidence=0.7,
                reasoning=f"Level 1 Recovery: Extending timeout. Root cause: {reflection.root_cause}",
            )
        elif "schema" in error or "validation" in error:
            return TaskResult(
                success=True,
                output_data={
                    "recovery_level": 1,
                    "action": "retry_with_relaxed_validation",
                    "corrected_parameters": {"strict_mode": False},
                    "root_cause": reflection.root_cause,
                },
                confidence=0.6,
                reasoning=f"Level 1 Recovery: Relaxing validation. Root cause: {reflection.root_cause}",
            )
        else:
            return TaskResult(
                success=True,
                output_data={
                    "recovery_level": 1,
                    "action": "simple_retry",
                    "corrected_parameters": {},
                    "root_cause": reflection.root_cause,
                },
                confidence=0.5,
                reasoning=f"Level 1 Recovery: Simple retry. Root cause: {reflection.root_cause}",
            )
