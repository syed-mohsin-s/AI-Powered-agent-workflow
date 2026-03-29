"""Run a strict end-to-end live P2P scenario and assert checkpoints.

Usage:
    python scripts/run_strict_p2p_live.py
    python scripts/run_strict_p2p_live.py --base-url http://127.0.0.1:8000 --timeout 45

The API server must already be running.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx


def build_payload(invoice_suffix: str) -> dict[str, Any]:
    return {
        "workflow_type": "p2p",
        "priority": 9,
        "input_data": {
            "type": "invoice",
            "content": (
                "Vendor: Acme Industrial Supplies\n"
                f"Invoice #: INV-2026-{invoice_suffix}\n"
                "Date: 03/29/2026\n"
                "PO #: PO-7782\n"
                "Total: $10820.00\n"
                "Payment Terms: Net 30"
            ),
            "vendor_name": "Acme Industrial Supplies",
            "invoice_number": f"INV-2026-{invoice_suffix}",
            "date": "03/29/2026",
            "total_amount": 10820.00,
            "currency": "USD",
            "po_number": "PO-7782",
            "line_items": [
                {"description": "Hydraulic Sensor", "quantity": 4, "unit_price": 1455.00, "total": 5820.00},
                {"description": "Controller Board", "quantity": 2, "unit_price": 2500.00, "total": 5000.00},
            ],
            "payment_terms": "Net 30",
            "requester": "Operations",
            "cost_center": "MFG-204",
        },
    }


def wait_for_completion(client: httpx.Client, base_url: str, workflow_id: str, timeout: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    last = None

    while time.time() < deadline:
        response = client.get(f"{base_url}/api/workflows/{workflow_id}")
        response.raise_for_status()
        last = response.json()

        if last.get("status") in {"completed", "failed", "escalated"}:
            return last

        time.sleep(0.8)

    if last is None:
        raise RuntimeError("No workflow status received while waiting for completion")
    return last


def evaluate_checkpoints(workflow: dict[str, Any]) -> dict[str, Any]:
    workflow_id = workflow["id"]
    output_data = workflow.get("output_data", {})

    extract_key = f"{workflow_id}_extract"
    validate_key = f"{workflow_id}_validate"
    match_key = f"{workflow_id}_match_po"
    decide_key = f"{workflow_id}_decide"
    guard_pay_key = f"{workflow_id}_guard_pay"
    pay_key = f"{workflow_id}_pay"
    guard_update_key = f"{workflow_id}_guard_update_erp"
    update_key = f"{workflow_id}_update_erp"
    verify_key = f"{workflow_id}_verify"

    checkpoints = {
        "workflow_completed": workflow.get("status") == "completed",
        "extract_has_invoice": bool(output_data.get(extract_key, {}).get("extracted", {}).get("invoice_number")),
        "policy_approved": bool(output_data.get(validate_key, {}).get("approved")),
        "decision_is_approve": output_data.get(decide_key, {}).get("decision") == "approve",
        "guard_pay_passed": bool(output_data.get(guard_pay_key, {}).get("guard_passed")),
        "erp_match_po_completed": output_data.get(match_key, {}).get("execution_result", {}).get("status") == "matched",
        "erp_payment_completed": output_data.get(pay_key, {}).get("execution_result", {}).get("status") == "completed",
        "guard_update_passed": bool(output_data.get(guard_update_key, {}).get("guard_passed")),
        "erp_update_completed": output_data.get(update_key, {}).get("execution_result", {}).get("status") == "updated",
        "verify_passed": bool(output_data.get(verify_key, {}).get("verified")),
    }

    failed = [name for name, ok in checkpoints.items() if not ok]

    return {
        "workflow_id": workflow_id,
        "final_status": workflow.get("status"),
        "checkpoints": checkpoints,
        "failed_checkpoints": failed,
        "verify_issues": output_data.get(verify_key, {}).get("issues", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict live P2P scenario and assert checkpoints")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Sentinel-AI API base URL")
    parser.add_argument("--timeout", type=int, default=45, help="Seconds to wait for workflow completion")
    parser.add_argument("--invoice-suffix", default="1109", help="Invoice suffix to make payload unique")
    args = parser.parse_args()

    payload = build_payload(args.invoice_suffix)

    with httpx.Client(timeout=15.0) as client:
        health = client.get(f"{args.base_url}/health")
        health.raise_for_status()

        submit = client.post(f"{args.base_url}/api/workflows/", json=payload)
        submit.raise_for_status()
        workflow_id = submit.json()["workflow_id"]

        workflow = wait_for_completion(client, args.base_url, workflow_id, args.timeout)

    result = evaluate_checkpoints(workflow)
    print(json.dumps(result, indent=2))

    return 0 if not result["failed_checkpoints"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
