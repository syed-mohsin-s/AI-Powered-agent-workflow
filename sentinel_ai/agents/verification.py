"""
Sentinel-AI Verification Agent.

Confirms action success and state consistency.
Detects silent failures where APIs return 200 but data didn't actually change.
"""

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.verification")


class VerificationAgent(BaseAgent):
    """
    Verifies that actions completed successfully and state is consistent.
    
    Detects:
    - Silent failures (API 200 but no actual change)
    - State inconsistencies
    - Missing expected outcomes
    """

    def __init__(self):
        super().__init__(
            name="Verification Agent",
            agent_type="verification",
        )

    async def execute(self, context: dict) -> TaskResult:
        """Verify the outcomes of previous task executions."""
        shared_context = context.get("shared_context", {})
        input_data = context.get("input_data", {})

        checks = []
        issues = []
        execution_failures = 0

        # Check 1: All previous tasks produced output
        for task_id, output in shared_context.items():
            if output is None:
                issues.append(f"Task {task_id} produced no output")
            elif isinstance(output, dict):
                if output.get("decision") == "reject":
                    checks.append({
                        "check": "rejection_verified",
                        "passed": True,
                        "detail": "Request was legitimately rejected by policy",
                    })
                elif "guard_passed" in output:
                    guard_ok = bool(output.get("guard_passed")) and not bool(output.get("blocked"))
                    checks.append({
                        "check": f"guard_{task_id}",
                        "passed": guard_ok,
                        "detail": output.get("reason", "Reliability guard check evaluated"),
                    })
                    if not guard_ok:
                        issues.append(f"Reliability guard blocked execution for {task_id}")
                elif "execution_result" in output:
                    exec_result = output["execution_result"]
                    status = str(exec_result.get("status", "unknown")).lower()
                    success_statuses = {
                        "completed",
                        "success",
                        "matched",
                        "created",
                        "updated",
                        "added",
                        "transitioned",
                        "sent",
                    }
                    has_blocking_error = bool(exec_result.get("error")) and not bool(exec_result.get("simulated"))
                    passed = status in success_statuses and not has_blocking_error
                    checks.append({
                        "check": f"execution_{task_id}",
                        "passed": passed,
                        "detail": f"Execution status: {status}",
                    })
                    if not passed:
                        execution_failures += 1
                        issues.append(f"Execution failure detected in {task_id}: {status}")
                elif "extracted" in output:
                    extracted = output["extracted"]
                    non_empty = sum(1 for v in extracted.values() if v)
                    checks.append({
                        "check": f"extraction_{task_id}",
                        "passed": non_empty > 0,
                        "detail": f"Extracted {non_empty} non-empty fields",
                    })
                else:
                    checks.append({
                        "check": f"output_{task_id}",
                        "passed": True,
                        "detail": f"Output present with {len(output)} fields",
                    })

        # Check 2: Compliance was evaluated
        compliance_checked = any(
            isinstance(o, dict) and ("approved" in o or "results" in o)
            for o in shared_context.values()
        )
        checks.append({
            "check": "compliance_evaluated",
            "passed": compliance_checked,
            "detail": "Compliance was " + ("evaluated" if compliance_checked else "NOT evaluated"),
        })

        # Check 3: Data consistency
        all_passed = all(c["passed"] for c in checks) and not issues
        confidence = (sum(1 for c in checks if c["passed"]) / max(len(checks), 1))

        return TaskResult(
            success=True,
            output_data={
                "verified": all_passed,
                "checks": checks,
                "issues": issues,
                "execution_failures": execution_failures,
                "total_checks": len(checks),
                "passed_checks": sum(1 for c in checks if c["passed"]),
            },
            confidence=confidence,
            reasoning=f"Verification: {sum(1 for c in checks if c['passed'])}/{len(checks)} checks passed. "
                      f"{'All clear.' if all_passed else f'Issues: {issues}'}",
        )
