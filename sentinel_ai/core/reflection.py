"""
Sentinel-AI Reflection Engine.

Provides structured reflection capabilities for analyzing
agent actions, execution outcomes, and failure root causes.
Used by both the execution agent (post-execution reflection)
and the recovery agent (failure analysis).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sentinel_ai.agents.base import get_llm_client
from sentinel_ai.utils.logger import get_logger

logger = get_logger("core.reflection")


@dataclass
class ReflectionResult:
    """Result of reflecting on an action or outcome."""

    assessment: str  # "success", "partial", "failed", "suboptimal"
    analysis: str  # Human-readable analysis
    confidence: float  # Confidence in the assessment
    suggestions: list[str] = field(default_factory=list)  # Improvement suggestions
    alternative_tools: list[str] = field(
        default_factory=list
    )  # Alternative tools to try
    root_cause: str = ""  # Root cause of failure (if applicable)
    can_switch_tool: bool = False  # Whether switching tools is recommended
    retry_recommended: bool = False  # Whether a retry might succeed
    data_quality_issues: list[str] = field(default_factory=list)  # Input data problems
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "assessment": self.assessment,
            "analysis": self.analysis,
            "confidence": self.confidence,
            "suggestions": self.suggestions,
            "alternative_tools": self.alternative_tools,
            "root_cause": self.root_cause,
            "can_switch_tool": self.can_switch_tool,
            "retry_recommended": self.retry_recommended,
            "data_quality_issues": self.data_quality_issues,
            "timestamp": self.timestamp.isoformat(),
        }


class ReflectionEngine:
    """
    Structured reflection on agent actions and outcomes.

    Provides two main capabilities:
    1. Post-execution reflection: Did this achieve the goal? Could it be better?
    2. Failure analysis: Root cause, alternative strategies, confidence.
    """

    def __init__(self):
        self._llm = get_llm_client()
        self._reflection_history: dict[str, list[ReflectionResult]] = (
            {}
        )  # workflow_id → reflections

    async def reflect_on_result(
        self,
        task_name: str,
        tool_used: str,
        result: dict,
        goal: str,
        context: dict,
    ) -> ReflectionResult:
        """
        Post-execution reflection: analyze whether the result achieves the goal.

        Used by the execution agent after tool execution to assess quality.
        """
        if self._llm.is_available:
            return await self._llm_reflect_on_result(
                task_name, tool_used, result, goal, context
            )

        return self._rule_based_result_reflection(result, goal)

    async def reflect_on_failure(
        self,
        task_name: str,
        error: str,
        tool_used: Optional[str],
        available_alternatives: list[str],
        attempts: int,
        context: dict,
    ) -> ReflectionResult:
        """
        Failure analysis: determine root cause and recommend recovery strategy.

        Used by the recovery agent to decide whether to retry, switch tools,
        or escalate.
        """
        if self._llm.is_available:
            return await self._llm_reflect_on_failure(
                task_name, error, tool_used, available_alternatives, attempts, context
            )

        return self._rule_based_failure_reflection(
            error, tool_used, available_alternatives, attempts
        )

    def record_reflection(self, workflow_id: str, reflection: ReflectionResult) -> None:
        """Store a reflection in the history for a workflow."""
        self._reflection_history.setdefault(workflow_id, []).append(reflection)

    def get_reflection_history(self, workflow_id: str) -> list[ReflectionResult]:
        """Get all reflections for a workflow."""
        return self._reflection_history.get(workflow_id, [])

    # -------------------------------------------------------------------
    # LLM-Based Reflection
    # -------------------------------------------------------------------

    async def _llm_reflect_on_result(
        self,
        task_name: str,
        tool_used: str,
        result: dict,
        goal: str,
        context: dict,
    ) -> ReflectionResult:
        """Use LLM to analyze execution results."""
        try:
            prompt = f"""Reflect on this task execution result:

Task: {task_name}
Goal: {goal}
Tool used: {tool_used}
Result: {json.dumps(result, indent=2, default=str)[:2000]}
Context: {json.dumps(context, indent=2, default=str)[:1000]}

Analyze:
1. Did the result achieve the goal?
2. Is the output quality sufficient?
3. Could a different tool have done better?
4. Any data quality issues in the input?

Return JSON with:
{{"assessment": "success|partial|suboptimal", "analysis": "...", "confidence": 0.0-1.0, "suggestions": [], "can_switch_tool": false, "data_quality_issues": []}}"""

            response = await self._llm.complete(
                prompt,
                system="You are an AI reflection engine. Analyze execution outcomes precisely.",
            )
            data = json.loads(self._clean_json(response))

            return ReflectionResult(
                assessment=data.get("assessment", "success"),
                analysis=data.get("analysis", "LLM reflection completed"),
                confidence=data.get("confidence", 0.8),
                suggestions=data.get("suggestions", []),
                can_switch_tool=data.get("can_switch_tool", False),
                data_quality_issues=data.get("data_quality_issues", []),
            )
        except Exception as e:
            logger.warning(f"LLM result reflection failed: {e}")
            return self._rule_based_result_reflection(result, goal)

    async def _llm_reflect_on_failure(
        self,
        task_name: str,
        error: str,
        tool_used: Optional[str],
        available_alternatives: list[str],
        attempts: int,
        context: dict,
    ) -> ReflectionResult:
        """Use LLM to analyze failures and suggest recovery."""
        try:
            prompt = f"""Analyze this task failure and suggest recovery:

Task: {task_name}
Error: {error}
Tool used: {tool_used or 'unknown'}
Attempts so far: {attempts}
Available alternative tools: {', '.join(available_alternatives) or 'none'}
Context: {json.dumps(context, indent=2, default=str)[:1500]}

Determine:
1. Root cause of the failure
2. Is this a transient error (retry might work)?
3. Would a different tool succeed?
4. Should we escalate to human?

Return JSON with:
{{"root_cause": "...", "analysis": "...", "confidence": 0.0-1.0, "can_switch_tool": true|false, "retry_recommended": true|false, "alternative_tools": ["tool_name"], "suggestions": []}}"""

            response = await self._llm.complete(
                prompt,
                system="You are an AI failure analyst. Diagnose errors precisely.",
            )
            data = json.loads(self._clean_json(response))

            return ReflectionResult(
                assessment="failed",
                analysis=data.get("analysis", "Failure analysis completed"),
                confidence=data.get("confidence", 0.5),
                root_cause=data.get("root_cause", error),
                can_switch_tool=data.get("can_switch_tool", False),
                retry_recommended=data.get("retry_recommended", False),
                alternative_tools=data.get("alternative_tools", []),
                suggestions=data.get("suggestions", []),
            )
        except Exception as e:
            logger.warning(f"LLM failure reflection failed: {e}")
            return self._rule_based_failure_reflection(
                error, tool_used, available_alternatives, attempts
            )

    # -------------------------------------------------------------------
    # Rule-Based Fallback Reflection
    # -------------------------------------------------------------------

    @staticmethod
    def _rule_based_result_reflection(result: dict, goal: str) -> ReflectionResult:
        """Simple rule-based reflection on results."""
        status = str(result.get("status", "")).lower()
        has_error = bool(result.get("error"))

        if status in ("success", "completed", "created", "updated") and not has_error:
            return ReflectionResult(
                assessment="success",
                analysis="Execution completed successfully",
                confidence=0.8,
            )
        elif has_error:
            return ReflectionResult(
                assessment="failed",
                analysis=f"Execution had errors: {result.get('error', 'unknown')}",
                confidence=0.6,
                can_switch_tool=True,
            )
        else:
            return ReflectionResult(
                assessment="partial",
                analysis="Execution completed but result quality is unclear",
                confidence=0.5,
                suggestions=["Verify output manually"],
            )

    @staticmethod
    def _rule_based_failure_reflection(
        error: str,
        tool_used: Optional[str],
        alternatives: list[str],
        attempts: int,
    ) -> ReflectionResult:
        """Rule-based failure analysis."""
        error_lower = error.lower()

        # Classify error type
        is_transient = any(
            kw in error_lower
            for kw in [
                "timeout",
                "temporary",
                "rate limit",
                "503",
                "429",
                "connection",
                "retry",
            ]
        )
        is_auth = any(
            kw in error_lower
            for kw in [
                "auth",
                "permission",
                "forbidden",
                "401",
                "403",
                "unauthorized",
            ]
        )
        is_validation = any(
            kw in error_lower
            for kw in [
                "schema",
                "validation",
                "invalid",
                "missing",
                "required",
            ]
        )
        is_not_found = any(
            kw in error_lower
            for kw in [
                "not found",
                "404",
                "unknown",
                "no such",
            ]
        )

        can_switch = bool(alternatives) and not is_auth
        retry_ok = is_transient and attempts < 3

        if is_transient:
            root_cause = "Transient infrastructure error"
        elif is_auth:
            root_cause = "Authentication or permission error"
        elif is_validation:
            root_cause = "Input validation or schema mismatch"
        elif is_not_found:
            root_cause = "Resource or endpoint not found"
        else:
            root_cause = "Unknown error"

        suggestions = []
        if retry_ok:
            suggestions.append("Retry with exponential backoff")
        if can_switch:
            suggestions.append(
                f"Switch to alternative tool: {alternatives[0] if alternatives else 'N/A'}"
            )
        if is_validation:
            suggestions.append("Validate and correct input data before retry")
        if is_auth:
            suggestions.append("Escalate to human — credentials may need refresh")

        return ReflectionResult(
            assessment="failed",
            analysis=f"Failure analysis: {root_cause}. "
            f"{'Retry recommended.' if retry_ok else 'Tool switch recommended.' if can_switch else 'Escalation recommended.'}",
            confidence=0.6 if is_transient else 0.4,
            root_cause=root_cause,
            can_switch_tool=can_switch,
            retry_recommended=retry_ok,
            alternative_tools=alternatives[:3] if can_switch else [],
            suggestions=suggestions,
        )

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _clean_json(text: str) -> str:
        """Clean potential markdown code blocks from LLM response."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1])
        return cleaned


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_reflection_engine: Optional[ReflectionEngine] = None


def get_reflection_engine() -> ReflectionEngine:
    """Get the global reflection engine singleton."""
    global _reflection_engine
    if _reflection_engine is None:
        _reflection_engine = ReflectionEngine()
    return _reflection_engine
