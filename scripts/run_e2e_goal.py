"""Run an end-to-end goal-driven workflow and assert dynamic planning and execution.

Usage:
    python scripts/run_e2e_goal.py
    python scripts/run_e2e_goal.py --base-url http://127.0.0.1:8000 --timeout 60

The API server must already be running.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx


def build_payload() -> dict[str, Any]:
    return {
        "goal": "Process Acme Corp invoice INV-2026-001 for $15000 and create a Jira ticket for tracking",
        "constraints": {
            "time_limit_minutes": 30
        },
        "priority": 5
    }


def wait_for_completion(client: httpx.Client, base_url: str, workflow_id: str, timeout: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    last = None

    print(f"Waiting for workflow {workflow_id} to complete (timeout: {timeout}s)...")
    while time.time() < deadline:
        response = client.get(f"{base_url}/api/workflows/{workflow_id}")
        response.raise_for_status()
        last = response.json()

        print(f"Status: {last.get('status')} - Tasks: {len(last.get('tasks', []))}")

        if last.get("status") in {"completed", "failed", "escalated"}:
            return last

        time.sleep(2)

    if last is None:
        raise RuntimeError("No workflow status received while waiting for completion")
    return last


def evaluate_checkpoints(workflow: dict[str, Any]) -> dict[str, Any]:
    workflow_id = workflow["id"]
    tasks = workflow.get("tasks", [])
    
    # Extract task names and statuses
    task_statuses = {t["task_name"]: t["status"] for t in tasks}
    
    checkpoints = {
        "workflow_completed": workflow.get("status") == "completed",
        "planner_task_ran": "Generate Execution Plan" in task_statuses,
        "dynamic_tasks_injected": len(tasks) > 1, # Planner + whatever it injected
        "all_tasks_completed": all(status in {"success", "skipped"} for status in task_statuses.values()) if tasks else False,
    }

    failed = [name for name, ok in checkpoints.items() if not ok]

    return {
        "workflow_id": workflow_id,
        "final_status": workflow.get("status"),
        "checkpoints": checkpoints,
        "failed_checkpoints": failed,
        "tasks_executed": len(tasks),
        "task_statuses": task_statuses
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live goal-driven scenario and assert checkpoints")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Sentinel-AI API base URL")
    parser.add_argument("--timeout", type=int, default=60, help="Seconds to wait for workflow completion")
    args = parser.parse_args()

    payload = build_payload()

    with httpx.Client(timeout=15.0) as client:
        print("Checking API health...")
        health = client.get(f"{args.base_url}/health")
        health.raise_for_status()
        print("API is healthy.")

        print("Submitting goal-driven workflow...")
        submit = client.post(f"{args.base_url}/api/workflows/goal", json=payload)
        submit.raise_for_status()
        workflow_id = submit.json()["workflow_id"]
        print(f"Submitted successfully. Workflow ID: {workflow_id}")

        workflow = wait_for_completion(client, args.base_url, workflow_id, args.timeout)

    result = evaluate_checkpoints(workflow)
    print("\n--- Test Results ---")
    print(json.dumps(result, indent=2))

    return 0 if not result["failed_checkpoints"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
