"""
Sentinel-AI Guardrail Agent.

Security-focused agent that runs policy checks and prompt-injection
detection on every incoming goal/input *before* it enters the workflow
pipeline.  Every check — pass or block — is recorded in the
cryptographic audit trail.

Distinct from ``reliability_guard.py`` (which handles idempotency /
action-level preflight).  This agent operates at the **content** level.
"""

import re
import unicodedata
from typing import Any

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.config import get_config
from sentinel_ai.models.audit import create_audit_record
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.guardrail")


# ---------------------------------------------------------------------------
# Injection pattern library
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[str, float, str]] = [
    # (regex_pattern, threat_weight, label)
    # -- Direct instruction override
    (r"ignore\s+(all\s+)?previous\s+instructions", 0.9, "instruction_override"),
    (r"ignore\s+above", 0.8, "instruction_override"),
    (r"disregard\s+(all\s+)?prior", 0.85, "instruction_override"),
    (r"forget\s+(everything|all|your)", 0.8, "instruction_override"),
    # -- System prompt extraction
    (r"(print|show|reveal|output|repeat)\s+(your\s+)?(system\s+prompt|instructions|rules)", 0.9, "prompt_extraction"),
    (r"what\s+are\s+your\s+(system\s+)?instructions", 0.7, "prompt_extraction"),
    # -- Role hijacking
    (r"you\s+are\s+now\s+(?:a|an)\s+", 0.7, "role_hijack"),
    (r"act\s+as\s+(?:a|an|if)\s+", 0.4, "role_hijack"),
    (r"pretend\s+(?:to\s+be|you\s+are)", 0.7, "role_hijack"),
    (r"switch\s+to\s+.+\s+mode", 0.6, "role_hijack"),
    # -- Encoded / obfuscated payloads
    (r"base64[:\s]", 0.5, "encoded_payload"),
    (r"\\x[0-9a-fA-F]{2}", 0.4, "encoded_payload"),
    (r"&#\d{2,4};", 0.5, "encoded_payload"),
    # -- Markdown / HTML injection
    (r"<script[^>]*>", 0.9, "html_injection"),
    (r"javascript:", 0.9, "html_injection"),
    (r"on\w+=", 0.5, "html_injection"),
    # -- Delimiter confusion
    (r"```\s*system", 0.8, "delimiter_attack"),
    (r"\[SYSTEM\]", 0.7, "delimiter_attack"),
    (r"<\|im_start\|>", 0.9, "delimiter_attack"),
    (r"### ?(SYSTEM|INSTRUCTION)", 0.7, "delimiter_attack"),
    # -- Data exfiltration
    (r"(send|post|fetch|curl|wget)\s+(to|http)", 0.6, "data_exfiltration"),
]

# PII patterns
_PII_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "ssn"),
    (r"\b\d{9}\b", "possible_ssn"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "email"),
    (r"\b(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}\b", "phone"),
    (r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "credit_card"),
]


class GuardrailAgent(BaseAgent):
    """Content-level security guardrail.

    Responsibilities:
    - Detect prompt-injection attempts in user-supplied text
    - Enforce content-length and blocked-keyword policies
    - Detect PII in inputs and flag for review
    - Sanitize inputs (Unicode normalisation, dangerous char stripping)
    - Log every decision to the cryptographic audit trail
    """

    def __init__(self):
        super().__init__(
            name="Guardrail Agent",
            agent_type="guardrail",
        )

    async def execute(self, context: dict) -> TaskResult:
        """Run guardrail checks on the task's input data."""
        config = get_config()
        gcfg = config.guardrail
        input_data = context.get("input_data", {})
        workflow_id = context.get("workflow_id", "")
        task_name = context.get("task_name", "guardrail_check")

        # Collect all textual content from input
        text_blob = self._extract_text(input_data)

        threats_detected: list[dict] = []
        policy_violations: list[str] = []
        threat_score = 0.0

        # 1. Content-length check
        if len(text_blob) > gcfg.max_input_length:
            policy_violations.append(
                f"Input length ({len(text_blob)}) exceeds limit ({gcfg.max_input_length})"
            )
            threat_score += 0.3

        # 2. Blocked keywords
        for keyword in gcfg.blocked_keywords:
            if keyword.lower() in text_blob.lower():
                policy_violations.append(f"Blocked keyword detected: '{keyword}'")
                threat_score += 0.4

        # 3. Prompt injection detection
        injection_hits = self._scan_injections(text_blob)
        threats_detected.extend(injection_hits)
        for hit in injection_hits:
            threat_score += hit["weight"]

        # 4. PII detection
        if gcfg.pii_detection_enabled:
            pii_hits = self._scan_pii(text_blob)
            if pii_hits:
                policy_violations.append(
                    f"PII detected: {', '.join(h['type'] for h in pii_hits)}"
                )
                threats_detected.extend(pii_hits)

        # 5. Sanitize input
        sanitized = self._sanitize(input_data)

        # Clamp threat score
        threat_score = min(threat_score, 1.0)

        # Decision
        blocked = threat_score >= gcfg.injection_threshold
        decision = "block" if blocked else "allow"

        # --- Audit record ---
        audit_context = f"workflow={workflow_id}, task={task_name}"
        reasoning = (
            f"Threat score {threat_score:.2f} "
            f"({'ABOVE' if blocked else 'below'} threshold {gcfg.injection_threshold}). "
            f"Threats: {len(threats_detected)}, Policy violations: {len(policy_violations)}"
        )

        create_audit_record(
            agent="guardrail",
            trigger_event="guardrail_check",
            context=audit_context,
            decision=decision,
            reasoning=reasoning,
            confidence=round(1.0 - threat_score, 2),
            action_taken=f"Input {'BLOCKED' if blocked else 'allowed'}",
            status="blocked" if blocked else "executed",
            why=f"Content security scan on task '{task_name}'",
            trade_offs="Blocking may prevent legitimate requests with unusual phrasing"
            if blocked
            else "",
        )

        if blocked:
            logger.warning(
                f"Guardrail BLOCKED input (score={threat_score:.2f})",
                extra_data={
                    "threats": threats_detected,
                    "violations": policy_violations,
                },
            )
            return TaskResult(
                success=False,
                error_message="Input blocked by security guardrail",
                output_data={
                    "guardrail_passed": False,
                    "threat_score": round(threat_score, 3),
                    "threats_detected": threats_detected,
                    "policy_violations": policy_violations,
                    "decision": "block",
                },
                confidence=round(1.0 - threat_score, 2),
                reasoning=reasoning,
            )

        logger.info(
            f"Guardrail PASSED input (score={threat_score:.2f})",
            extra_data={"threats_count": len(threats_detected)},
        )
        return TaskResult(
            success=True,
            output_data={
                "guardrail_passed": True,
                "threat_score": round(threat_score, 3),
                "threats_detected": threats_detected,
                "policy_violations": policy_violations,
                "sanitized_input": sanitized,
                "decision": "allow",
            },
            confidence=round(1.0 - threat_score, 2),
            reasoning=reasoning,
        )

    # -------------------------------------------------------------------
    # Injection scanning
    # -------------------------------------------------------------------

    @staticmethod
    def _scan_injections(text: str) -> list[dict]:
        """Scan text for known injection patterns."""
        hits: list[dict] = []
        text_lower = text.lower()
        for pattern, weight, label in _INJECTION_PATTERNS:
            matches = re.findall(pattern, text_lower)
            if matches:
                hits.append(
                    {
                        "type": "injection",
                        "label": label,
                        "weight": weight,
                        "matches": len(matches) if isinstance(matches, list) else 1,
                    }
                )
        return hits

    # -------------------------------------------------------------------
    # PII scanning
    # -------------------------------------------------------------------

    @staticmethod
    def _scan_pii(text: str) -> list[dict]:
        """Scan for personally identifiable information patterns."""
        hits: list[dict] = []
        for pattern, pii_type in _PII_PATTERNS:
            if re.search(pattern, text):
                hits.append({"type": pii_type, "weight": 0.0})
        return hits

    # -------------------------------------------------------------------
    # Sanitization
    # -------------------------------------------------------------------

    def _sanitize(self, data: Any) -> Any:
        """Recursively sanitize input data."""
        if isinstance(data, str):
            return self._sanitize_string(data)
        if isinstance(data, dict):
            return {k: self._sanitize(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._sanitize(item) for item in data]
        return data

    @staticmethod
    def _sanitize_string(s: str) -> str:
        """Normalize Unicode and strip dangerous sequences."""
        # Normalize to NFC to prevent homograph attacks
        s = unicodedata.normalize("NFC", s)
        # Strip null bytes
        s = s.replace("\x00", "")
        # Strip common HTML injection vectors
        s = re.sub(r"<script[^>]*>.*?</script>", "", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(r"javascript:", "", s, flags=re.IGNORECASE)
        return s

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _extract_text(data: Any, max_depth: int = 5) -> str:
        """Recursively extract all string values from nested data."""
        if max_depth <= 0:
            return ""
        parts: list[str] = []
        if isinstance(data, str):
            parts.append(data)
        elif isinstance(data, dict):
            for v in data.values():
                parts.append(GuardrailAgent._extract_text(v, max_depth - 1))
        elif isinstance(data, (list, tuple)):
            for item in data:
                parts.append(GuardrailAgent._extract_text(item, max_depth - 1))
        return " ".join(parts)
