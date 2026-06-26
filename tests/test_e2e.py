"""End-to-end integration tests for Sentinel-AI workflows."""

import asyncio
import pytest
import httpx
from httpx import ASGITransport
from typing import Any

from sentinel_ai.main import app
from sentinel_ai.core.engine import get_engine
from sentinel_ai.workflows.goal_workflow import create_goal_workflow
from sentinel_ai.models.workflow import TaskStatus, WorkflowStatus


@pytest.fixture
async def app_lifespan():
    """Fixture to handle the FastAPI app lifecycle (lifespan events)."""
    async with app.router.lifespan_context(app):
        yield app


@pytest.mark.asyncio
async def test_local_e2e_goal(app_lifespan):
    """Run local goal-driven workflow using the engine directly (no HTTP)."""
    engine = get_engine()
    
    goal = "Process Acme Corp invoice INV-2026-001 for $15000 and create a Jira ticket for tracking"
    
    workflow, tasks = create_goal_workflow(
        goal=goal,
        constraints={"time_limit_minutes": 30},
        priority=5,
    )
    
    workflow_id = await engine.submit_workflow(workflow, tasks, sla_minutes=60)
    assert workflow_id is not None
    
    # Wait for completion
    timeout = 60
    deadline = asyncio.get_event_loop().time() + timeout
    
    while asyncio.get_event_loop().time() < deadline:
        wf = engine.get_workflow(workflow_id)
        status = wf.status.value
        
        if status in {"completed", "failed", "escalated"}:
            break
            
        await asyncio.sleep(0.5)
        
    wf = engine.get_workflow(workflow_id)
    assert wf.status == WorkflowStatus.COMPLETED
    assert len(wf.tasks) == 6
    
    # Checkpoints
    task_statuses = {t.name: t.status for t in wf.tasks.values()}
    assert "Generate Execution Plan" in task_statuses
    assert all(status == TaskStatus.SUCCESS for status in task_statuses.values())


@pytest.mark.asyncio
async def test_http_e2e_p2p(app_lifespan):
    """Run strict end-to-end P2P workflow via HTTP API."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # Check health
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
        
        # Build strict P2P payload
        payload = {
            "workflow_type": "p2p",
            "priority": 9,
            "input_data": {
                "type": "invoice",
                "content": (
                    "Vendor: Acme Industrial Supplies\n"
                    "Invoice #: INV-2026-1109\n"
                    "Date: 03/29/2026\n"
                    "PO #: PO-7782\n"
                    "Total: $10820.00\n"
                    "Payment Terms: Net 30"
                ),
                "vendor_name": "Acme Industrial Supplies",
                "invoice_number": "INV-2026-1109",
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
        
        # Submit
        response = await client.post("/api/workflows/", json=payload)
        assert response.status_code == 200
        workflow_id = response.json()["workflow_id"]
        assert workflow_id is not None
        
        # Poll for completion
        timeout = 60
        deadline = asyncio.get_event_loop().time() + timeout
        last_state = {}
        
        while asyncio.get_event_loop().time() < deadline:
            response = await client.get(f"/api/workflows/{workflow_id}")
            assert response.status_code == 200
            last_state = response.json()
            if last_state.get("status") in {"completed", "failed", "escalated"}:
                break
            await asyncio.sleep(0.5)
            
        assert last_state.get("status") == "completed"
        
        output_data = last_state.get("output_data") or {}
        extract_key = f"{workflow_id}_extract"
        validate_key = f"{workflow_id}_validate"
        match_key = f"{workflow_id}_match_po"
        decide_key = f"{workflow_id}_decide"
        guard_pay_key = f"{workflow_id}_guard_pay"
        pay_key = f"{workflow_id}_pay"
        guard_update_key = f"{workflow_id}_guard_update_erp"
        update_key = f"{workflow_id}_update_erp"
        verify_key = f"{workflow_id}_verify"
        
        # Assert checkpoints
        assert output_data.get(extract_key, {}).get("extracted", {}).get("invoice_number") == "INV-2026-1109"
        assert output_data.get(validate_key, {}).get("approved") is True
        assert output_data.get(decide_key, {}).get("decision") == "approve"
        assert output_data.get(guard_pay_key, {}).get("guard_passed") is True
        assert output_data.get(match_key, {}).get("execution_result", {}).get("status") == "matched"
        assert output_data.get(pay_key, {}).get("execution_result", {}).get("status") == "completed"
        assert output_data.get(guard_update_key, {}).get("guard_passed") is True
        assert output_data.get(update_key, {}).get("execution_result", {}).get("status") == "updated"
        assert output_data.get(verify_key, {}).get("verified") is True


@pytest.mark.asyncio
async def test_http_e2e_goal(app_lifespan):
    """Run goal-driven workflow via HTTP API."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # Check health
        health = await client.get("/health")
        assert health.status_code == 200
        
        # Build goal payload
        payload = {
            "goal": "Process Acme Corp invoice INV-2026-001 for $15000 and create a Jira ticket for tracking",
            "constraints": {
                "time_limit_minutes": 30
            },
            "priority": 5
        }
        
        # Submit
        response = await client.post("/api/workflows/goal", json=payload)
        assert response.status_code == 200
        workflow_id = response.json()["workflow_id"]
        assert workflow_id is not None
        
        # Poll for completion
        timeout = 60
        deadline = asyncio.get_event_loop().time() + timeout
        last_state = {}
        
        while asyncio.get_event_loop().time() < deadline:
            response = await client.get(f"/api/workflows/{workflow_id}")
            assert response.status_code == 200
            last_state = response.json()
            if last_state.get("status") in {"completed", "failed", "escalated"}:
                break
            await asyncio.sleep(0.5)
            
        assert last_state.get("status") == "completed"
        
        tasks = last_state.get("tasks", [])
        task_statuses = {t["task_name"]: t["status"] for t in tasks}
        
        assert "Generate Execution Plan" in task_statuses
        assert len(tasks) > 1
        assert all(status == "success" for status in task_statuses.values())
