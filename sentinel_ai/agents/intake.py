"""
Sentinel-AI Intake / Retrieval Agent.

Extracts structured data from various input formats:
PDFs, emails, transcripts, JSON, and ERP/CRM inputs.
"""

import json
import re
from typing import Any

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.intake")


class IntakeAgent(BaseAgent):
    """
    Data extraction agent that processes diverse input formats.
    
    Uses LLM for intelligent extraction when available,
    falls back to regex/rule-based extraction.
    """

    def __init__(self):
        super().__init__(
            name="Intake / Retrieval Agent",
            agent_type="intake",
        )
        self._extractors = {
            "invoice": self._extract_invoice,
            "meeting_transcript": self._extract_meeting,
            "onboarding_request": self._extract_onboarding,
            "contract": self._extract_contract,
            "email": self._extract_email,
        }

    async def execute(self, context: dict) -> TaskResult:
        """Extract structured data from input."""
        input_data = context.get("input_data", {})
        input_type = input_data.get("type", "generic")
        content = input_data.get("content", "")

        if not content and not input_data:
            return TaskResult(
                success=False,
                error_message="No input content provided",
                confidence=0.0,
                reasoning="Cannot extract data from empty input",
            )

        # Try LLM extraction first
        if self._llm.is_available and content:
            extracted = await self._llm_extract(input_type, content)
            if extracted:
                return TaskResult(
                    success=True,
                    output_data={"extracted": extracted, "method": "llm", "input_type": input_type},
                    confidence=0.9,
                    reasoning=f"LLM extracted {len(extracted)} fields from {input_type}",
                )

        # Fallback to rule-based extraction
        extractor = self._extractors.get(input_type, self._extract_generic)
        extracted = await extractor(input_data)

        return TaskResult(
            success=True,
            output_data={"extracted": extracted, "method": "rule_based", "input_type": input_type},
            confidence=0.75 if extracted else 0.3,
            reasoning=f"Rule-based extraction found {len(extracted)} fields from {input_type}",
        )

    async def _llm_extract(self, input_type: str, content: str) -> dict:
        """Use LLM for intelligent data extraction."""
        schemas = {
            "invoice": "vendor_name, invoice_number, date, line_items[{description, quantity, unit_price, total}], subtotal, tax, total_amount, currency, po_number, payment_terms",
            "meeting_transcript": "decisions[{text, made_by}], action_items[{task, assignee, deadline}], attendees[], key_topics[], next_meeting_date",
            "onboarding_request": "employee_name, email, position, department, start_date, manager, equipment_needed[], access_required[]",
            "contract": "parties[], contract_type, effective_date, expiration_date, value, key_terms[], renewal_terms, governing_law",
        }
        schema = schemas.get(input_type, "key_fields, values, metadata")

        try:
            response = await self.llm_extract(content[:4000], schema)
            return json.loads(response)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"LLM extraction failed: {e}")
            return {}

    async def _extract_invoice(self, data: dict) -> dict:
        """Rule-based invoice extraction."""
        content = data.get("content", "")
        return {
            "vendor_name": data.get("vendor_name", self._extract_field(content, r"vendor[:\s]+(.+?)[\n]")),
            "invoice_number": data.get("invoice_number", self._extract_field(content, r"invoice\s*#?\s*[:\s]+(\S+)")),
            "date": data.get("date", self._extract_field(content, r"date[:\s]+(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})")),
            "total_amount": data.get("total_amount", self._extract_number(content, r"total[:\s]+\$?([\d,]+\.?\d*)")),
            "currency": data.get("currency", "USD"),
            "po_number": data.get("po_number", self._extract_field(content, r"PO\s*#?\s*[:\s]+(\S+)")),
            "line_items": data.get("line_items", []),
        }

    async def _extract_meeting(self, data: dict) -> dict:
        """Rule-based meeting transcript extraction."""
        content = data.get("content", "")
        
        # Simple decision detection
        decisions = []
        for line in content.split("\n"):
            line_lower = line.lower()
            if any(kw in line_lower for kw in ["decided", "agreed", "approved", "will proceed"]):
                decisions.append({"text": line.strip(), "made_by": "group"})

        # Simple action item detection
        action_items = []
        for line in content.split("\n"):
            line_lower = line.lower()
            if any(kw in line_lower for kw in ["action:", "todo:", "task:", "will do", "assigned to"]):
                action_items.append({"task": line.strip(), "assignee": "TBD", "deadline": "TBD"})

        return {
            "decisions": decisions,
            "action_items": action_items,
            "attendees": data.get("attendees", []),
            "key_topics": data.get("topics", []),
        }

    async def _extract_onboarding(self, data: dict) -> dict:
        """Extract onboarding request data."""
        return {
            "employee_name": data.get("employee_name", ""),
            "email": data.get("email", ""),
            "position": data.get("position", ""),
            "department": data.get("department", ""),
            "start_date": data.get("start_date", ""),
            "manager": data.get("manager", ""),
            "equipment_needed": data.get("equipment_needed", ["laptop", "monitor"]),
            "access_required": data.get("access_required", ["email", "slack", "github"]),
        }

    async def _extract_contract(self, data: dict) -> dict:
        """Extract contract data."""
        content = data.get("content", "")
        return {
            "parties": data.get("parties", []),
            "contract_type": data.get("contract_type", "service_agreement"),
            "effective_date": data.get("effective_date", ""),
            "expiration_date": data.get("expiration_date", ""),
            "value": data.get("value", 0),
            "key_terms": data.get("key_terms", []),
        }

    async def _extract_email(self, data: dict) -> dict:
        """Extract email data."""
        return {
            "from": data.get("from", ""),
            "to": data.get("to", ""),
            "subject": data.get("subject", ""),
            "body": data.get("body", data.get("content", "")),
            "attachments": data.get("attachments", []),
        }

    async def _extract_generic(self, data: dict) -> dict:
        """Generic extraction — pass through known fields."""
        return {k: v for k, v in data.items() if k != "content"}

    @staticmethod
    def _extract_field(text: str, pattern: str) -> str:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_number(text: str, pattern: str) -> float:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                return 0.0
        return 0.0
