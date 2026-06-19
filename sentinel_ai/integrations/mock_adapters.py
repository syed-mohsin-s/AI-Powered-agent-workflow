"""Mock integration adapters for ERP, Email, and ServiceNow."""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Callable

from sentinel_ai.core.tool_registry import ToolCapability
from sentinel_ai.integrations.base import BaseIntegration
from sentinel_ai.utils.logger import get_logger

logger = get_logger("integrations.mock")


class ERPAdapter(BaseIntegration):
    """Mock ERP/SAP adapter."""

    def __init__(self, delay_ms: int = 500):
        super().__init__(name="ERP/SAP", integration_type="erp")
        self._delay = delay_ms / 1000.0

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def execute(self, action: str, data: dict) -> dict:
        await asyncio.sleep(self._delay)
        actions = {
            "create_payment": {
                "status": "completed",
                "payment_id": f"PAY-{uuid.uuid4().hex[:8].upper()}",
                "amount": data.get("total_amount", 0),
            },
            "update_record": {
                "status": "updated",
                "record_id": data.get(
                    "record_id", f"REC-{uuid.uuid4().hex[:8].upper()}"
                ),
            },
            "create_po": {
                "status": "created",
                "po_number": f"PO-{uuid.uuid4().hex[:8].upper()}",
            },
            "match_po": {
                "status": "matched",
                "match_confidence": 0.95,
                "po_number": data.get("po_number", "PO-UNKNOWN"),
            },
            "provision_accounts": {
                "status": "provisioned",
                "accounts": ["email", "erp_access", "directory"],
            },
        }
        result = actions.get(action, {"status": "completed", "action": action})
        result["simulated"] = True
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return result

    async def health_check(self) -> dict:
        return {"status": "healthy", "connected": True, "simulated": True}

    def get_tool_capabilities(self) -> list[tuple[ToolCapability, Callable]]:
        """Declare ERP tools with rich metadata."""
        return [
            (
                ToolCapability(
                    name="erp_create_payment",
                    description="Create a payment record in the ERP system. Processes invoice payments, vendor payments, and refunds.",
                    provider="erp",
                    category="payment",
                    capabilities=[
                        "create",
                        "payment",
                        "erp",
                        "invoice",
                        "pay",
                        "financial",
                    ],
                    input_schema={
                        "required": ["total_amount"],
                        "optional": [
                            "vendor_name",
                            "invoice_number",
                            "currency",
                            "po_number",
                        ],
                    },
                    output_schema={
                        "payment_id": "str",
                        "status": "str",
                        "amount": "float",
                    },
                    estimated_latency_ms=500,
                    cost_tier="medium",
                    permissions_required=["write:erp:payments"],
                ),
                self._make_action_executor("create_payment"),
            ),
            (
                ToolCapability(
                    name="erp_match_po",
                    description="Match an invoice against a purchase order in the ERP system. Performs three-way matching (PO, receipt, invoice).",
                    provider="erp",
                    category="payment",
                    capabilities=[
                        "match",
                        "purchase_order",
                        "po",
                        "erp",
                        "invoice",
                        "verify",
                    ],
                    input_schema={
                        "required": ["po_number"],
                        "optional": ["invoice_number", "total_amount", "vendor_name"],
                    },
                    output_schema={
                        "match_confidence": "float",
                        "po_number": "str",
                        "status": "str",
                    },
                    estimated_latency_ms=500,
                    cost_tier="low",
                    permissions_required=["read:erp:purchase_orders"],
                ),
                self._make_action_executor("match_po"),
            ),
            (
                ToolCapability(
                    name="erp_update_record",
                    description="Update an existing record in the ERP system. Supports updating invoices, POs, vendor records, and general ledger entries.",
                    provider="erp",
                    category="data_management",
                    capabilities=["update", "record", "erp", "modify", "edit"],
                    input_schema={
                        "required": [],
                        "optional": ["record_id", "fields"],
                    },
                    output_schema={"record_id": "str", "status": "str"},
                    estimated_latency_ms=500,
                    cost_tier="low",
                    permissions_required=["write:erp:records"],
                ),
                self._make_action_executor("update_record"),
            ),
            (
                ToolCapability(
                    name="erp_create_po",
                    description="Create a new purchase order in the ERP system.",
                    provider="erp",
                    category="procurement",
                    capabilities=[
                        "create",
                        "purchase_order",
                        "po",
                        "erp",
                        "procurement",
                    ],
                    input_schema={
                        "required": ["vendor_name"],
                        "optional": ["items", "total_amount", "currency"],
                    },
                    output_schema={"po_number": "str", "status": "str"},
                    estimated_latency_ms=500,
                    cost_tier="medium",
                    permissions_required=["write:erp:purchase_orders"],
                ),
                self._make_action_executor("create_po"),
            ),
            (
                ToolCapability(
                    name="erp_provision_accounts",
                    description="Provision user accounts and access in the ERP system for new employees.",
                    provider="erp",
                    category="provisioning",
                    capabilities=[
                        "provision",
                        "accounts",
                        "erp",
                        "access",
                        "onboarding",
                        "user",
                    ],
                    input_schema={
                        "required": ["employee_name"],
                        "optional": ["email", "department", "role"],
                    },
                    output_schema={"accounts": "list", "status": "str"},
                    estimated_latency_ms=500,
                    cost_tier="low",
                    permissions_required=["write:erp:accounts"],
                ),
                self._make_action_executor("provision_accounts"),
            ),
        ]


class EmailAdapter(BaseIntegration):
    """Mock email adapter."""

    def __init__(self, delay_ms: int = 200):
        super().__init__(name="Email", integration_type="email")
        self._delay = delay_ms / 1000.0
        self._sent_emails: list[dict] = []

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def execute(self, action: str, data: dict) -> dict:
        await asyncio.sleep(self._delay)
        if action == "send":
            email = {
                "id": str(uuid.uuid4()),
                "to": data.get("to", ""),
                "subject": data.get("subject", "Sentinel-AI Notification"),
                "body": data.get("body", ""),
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            self._sent_emails.append(email)
            return {"status": "sent", "email_id": email["id"], "simulated": True}
        elif action == "send_reminder":
            return {
                "status": "reminder_sent",
                "to": data.get("to", ""),
                "simulated": True,
            }
        return {"status": "completed", "simulated": True}

    async def health_check(self) -> dict:
        return {"status": "healthy", "connected": True, "simulated": True}

    def get_tool_capabilities(self) -> list[tuple[ToolCapability, Callable]]:
        """Declare email tools with rich metadata."""
        return [
            (
                ToolCapability(
                    name="email_send",
                    description="Send an email notification to one or more recipients. Supports HTML body, attachments, and CC/BCC.",
                    provider="email",
                    category="communication",
                    capabilities=[
                        "send",
                        "email",
                        "notify",
                        "message",
                        "communication",
                    ],
                    input_schema={
                        "required": ["to", "subject"],
                        "optional": ["body", "cc", "bcc", "attachments"],
                    },
                    output_schema={"email_id": "str", "status": "str"},
                    estimated_latency_ms=200,
                    cost_tier="free",
                    permissions_required=["write:email"],
                ),
                self._make_action_executor("send"),
            ),
            (
                ToolCapability(
                    name="email_send_reminder",
                    description="Send a reminder email to a specific recipient about a pending action or deadline.",
                    provider="email",
                    category="communication",
                    capabilities=["send", "reminder", "email", "notify", "follow_up"],
                    input_schema={
                        "required": ["to"],
                        "optional": ["subject", "body", "deadline"],
                    },
                    output_schema={"status": "str"},
                    estimated_latency_ms=200,
                    cost_tier="free",
                    permissions_required=["write:email"],
                ),
                self._make_action_executor("send_reminder"),
            ),
        ]


class ServiceNowAdapter(BaseIntegration):
    """Mock ServiceNow adapter."""

    def __init__(self, delay_ms: int = 400):
        super().__init__(name="ServiceNow", integration_type="itsm")
        self._delay = delay_ms / 1000.0

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def execute(self, action: str, data: dict) -> dict:
        await asyncio.sleep(self._delay)
        actions = {
            "create_incident": {
                "status": "created",
                "incident_number": f"INC-{uuid.uuid4().hex[:8].upper()}",
                "priority": data.get("priority", "Medium"),
            },
            "create_request": {
                "status": "created",
                "request_number": f"REQ-{uuid.uuid4().hex[:8].upper()}",
            },
            "update_ticket": {
                "status": "updated",
                "ticket_id": data.get("ticket_id", ""),
            },
            "close_ticket": {
                "status": "closed",
                "ticket_id": data.get("ticket_id", ""),
            },
        }
        result = actions.get(action, {"status": "completed", "action": action})
        result["simulated"] = True
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return result

    async def health_check(self) -> dict:
        return {"status": "healthy", "connected": True, "simulated": True}

    def get_tool_capabilities(self) -> list[tuple[ToolCapability, Callable]]:
        """Declare ServiceNow tools with rich metadata."""
        return [
            (
                ToolCapability(
                    name="servicenow_create_incident",
                    description="Create an incident ticket in ServiceNow for tracking issues, outages, or problems.",
                    provider="servicenow",
                    category="issue_tracking",
                    capabilities=[
                        "create",
                        "incident",
                        "ticket",
                        "servicenow",
                        "itsm",
                        "issue",
                    ],
                    input_schema={
                        "required": ["description"],
                        "optional": ["priority", "category", "assigned_to", "urgency"],
                    },
                    output_schema={"incident_number": "str", "status": "str"},
                    estimated_latency_ms=400,
                    cost_tier="low",
                    permissions_required=["write:servicenow:incidents"],
                ),
                self._make_action_executor("create_incident"),
            ),
            (
                ToolCapability(
                    name="servicenow_create_request",
                    description="Create a service request in ServiceNow for provisioning, access, or equipment needs.",
                    provider="servicenow",
                    category="provisioning",
                    capabilities=[
                        "create",
                        "request",
                        "servicenow",
                        "service",
                        "provision",
                    ],
                    input_schema={
                        "required": ["request_type"],
                        "optional": ["description", "urgency", "requested_for"],
                    },
                    output_schema={"request_number": "str", "status": "str"},
                    estimated_latency_ms=400,
                    cost_tier="low",
                    permissions_required=["write:servicenow:requests"],
                ),
                self._make_action_executor("create_request"),
            ),
            (
                ToolCapability(
                    name="servicenow_update_ticket",
                    description="Update an existing ticket in ServiceNow with new information, status changes, or comments.",
                    provider="servicenow",
                    category="issue_tracking",
                    capabilities=["update", "ticket", "servicenow", "modify"],
                    input_schema={
                        "required": ["ticket_id"],
                        "optional": ["status", "comment", "assigned_to"],
                    },
                    output_schema={"ticket_id": "str", "status": "str"},
                    estimated_latency_ms=400,
                    cost_tier="low",
                    permissions_required=["write:servicenow:tickets"],
                ),
                self._make_action_executor("update_ticket"),
            ),
            (
                ToolCapability(
                    name="servicenow_close_ticket",
                    description="Close a resolved ticket in ServiceNow with a resolution summary.",
                    provider="servicenow",
                    category="issue_tracking",
                    capabilities=["close", "ticket", "servicenow", "resolve"],
                    input_schema={
                        "required": ["ticket_id"],
                        "optional": ["resolution_notes"],
                    },
                    output_schema={"ticket_id": "str", "status": "str"},
                    estimated_latency_ms=400,
                    cost_tier="low",
                    permissions_required=["write:servicenow:tickets"],
                ),
                self._make_action_executor("close_ticket"),
            ),
        ]
