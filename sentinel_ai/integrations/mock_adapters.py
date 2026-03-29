"""Mock integration adapters for ERP, Email, and ServiceNow."""

import asyncio
import uuid
from datetime import datetime, timezone

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
            "create_payment": {"status": "completed", "payment_id": f"PAY-{uuid.uuid4().hex[:8].upper()}", "amount": data.get("total_amount", 0)},
            "update_record": {"status": "updated", "record_id": data.get("record_id", f"REC-{uuid.uuid4().hex[:8].upper()}")},
            "create_po": {"status": "created", "po_number": f"PO-{uuid.uuid4().hex[:8].upper()}"},
            "match_po": {"status": "matched", "match_confidence": 0.95, "po_number": data.get("po_number", "PO-UNKNOWN")},
            "provision_accounts": {"status": "provisioned", "accounts": ["email", "erp_access", "directory"]},
        }
        result = actions.get(action, {"status": "completed", "action": action})
        result["simulated"] = True
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return result

    async def health_check(self) -> dict:
        return {"status": "healthy", "connected": True, "simulated": True}


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
            return {"status": "reminder_sent", "to": data.get("to", ""), "simulated": True}
        return {"status": "completed", "simulated": True}

    async def health_check(self) -> dict:
        return {"status": "healthy", "connected": True, "simulated": True}


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
            "create_incident": {"status": "created", "incident_number": f"INC-{uuid.uuid4().hex[:8].upper()}", "priority": data.get("priority", "Medium")},
            "create_request": {"status": "created", "request_number": f"REQ-{uuid.uuid4().hex[:8].upper()}"},
            "update_ticket": {"status": "updated", "ticket_id": data.get("ticket_id", "")},
            "close_ticket": {"status": "closed", "ticket_id": data.get("ticket_id", "")},
        }
        result = actions.get(action, {"status": "completed", "action": action})
        result["simulated"] = True
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return result

    async def health_check(self) -> dict:
        return {"status": "healthy", "connected": True, "simulated": True}
