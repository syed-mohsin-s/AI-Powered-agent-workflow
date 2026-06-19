"""
Sentinel-AI Skill Definitions.

A Skill is a context-specific bundle of:
- System prompt (specialized instructions for the LLM)
- Preferred tools (tools that work best for this skill)
- Validation rules (input data requirements)
- Trigger conditions (when to activate this skill)
"""

from dataclasses import dataclass, field


@dataclass
class Skill:
    """A loadable skill that customizes agent behavior for specific contexts."""

    name: str
    description: str

    # When to activate this skill
    triggers: dict = field(default_factory=dict)
    # - workflow_types: list[str] — activate for these workflow types
    # - task_names: list[str] — keywords in task name
    # - data_contains: list[str] — keys present in input data

    # Specialized system prompt for LLM
    system_prompt: str = ""

    # Preferred tools for this skill
    tools_preferred: list[str] = field(default_factory=list)

    # Validation rules for input data
    validation_rules: list[dict] = field(default_factory=list)

    # Priority (higher = loaded first in prompt composition)
    priority: int = 5

    def matches_context(
        self,
        workflow_type: str = "",
        task_name: str = "",
        data_keys: set[str] = None,
    ) -> float:
        """
        Compute how well this skill matches the given context.

        Returns a score in [0.0, 1.0]. Higher means better match.
        """
        if data_keys is None:
            data_keys = set()

        score = 0.0
        checks = 0

        # Check workflow type match
        wf_types = self.triggers.get("workflow_types", [])
        if wf_types:
            checks += 1
            if workflow_type in wf_types:
                score += 1.0

        # Check task name keywords
        task_keywords = self.triggers.get("task_names", [])
        if task_keywords:
            checks += 1
            task_lower = task_name.lower()
            matched = sum(1 for kw in task_keywords if kw.lower() in task_lower)
            if matched:
                score += matched / len(task_keywords)

        # Check data key presence
        data_contains = self.triggers.get("data_contains", [])
        if data_contains:
            checks += 1
            present = sum(1 for key in data_contains if key in data_keys)
            if present:
                score += present / len(data_contains)

        return score / max(checks, 1)
