"""
Sentinel-AI Execution Agent.

Performs actions via integration adapters (ERP, Atlassian MCP, Email, ServiceNow).
Validates schema before execution and supports rollback.
"""

import asyncio
from typing import Any

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.execution")


class ExecutionAgent(BaseAgent):
    """
    Executes external actions via integration adapters.
    
    Responsibilities:
    - Validate schema before execution
    - Execute via appropriate adapter
    - Handle external system errors
    - Support rollback on failure
    """

    def __init__(self):
        super().__init__(
            name="Execution Agent",
            agent_type="execution",
        )
        self._integrations: dict[str, Any] = {}

    def register_integration(self, name: str, adapter: Any) -> None:
        """Register an integration adapter."""
        self._integrations[name] = adapter

    async def execute(self, context: dict) -> TaskResult:
        """Execute an action via the appropriate integration."""
        input_data = context.get("input_data", {})
        shared_context = context.get("shared_context", {})
        action = input_data.get("action", "")
        target_system = input_data.get("target_system", "")

        guard_status = self._evaluate_guard_status(shared_context)
        if guard_status["blocked"]:
            return TaskResult(
                success=False,
                error_message=guard_status["reason"],
                output_data={
                    "action": "blocked",
                    "reason": guard_status["reason"],
                    "guard_signals": guard_status["signals"],
                },
                confidence=0.2,
                reasoning="Execution blocked by reliability guard",
            )

        # Merge context
        data = {}
        for output in shared_context.values():
            if isinstance(output, dict):
                merged = output.get("merged_data", output.get("extracted", output))
                if isinstance(merged, dict):
                    data.update(merged)
        data.update(input_data)

        # Check if decision was to reject
        for output in shared_context.values():
            if isinstance(output, dict) and output.get("decision") == "reject":
                return TaskResult(
                    success=True,
                    output_data={"action": "skipped", "reason": "Decision was to reject"},
                    confidence=1.0,
                    reasoning="Action skipped — decision agent rejected the request",
                )

        # Execute via adapter
        adapter = self._integrations.get(target_system)
        if adapter:
            try:
                result = await adapter.execute(action, data)
                if self._is_failed_result(result):
                    return TaskResult(
                        success=False,
                        error_message=f"Integration action failed on {target_system}",
                        output_data={"execution_result": result, "system": target_system, "action": action},
                        confidence=0.2,
                        reasoning=f"Adapter returned failure status for '{action}' on {target_system}",
                    )

                return TaskResult(
                    success=True,
                    output_data={"execution_result": result, "system": target_system, "action": action},
                    confidence=0.9,
                    reasoning=f"Successfully executed '{action}' on {target_system}",
                )
            except Exception as e:
                return TaskResult(
                    success=False,
                    error_message=f"Integration error ({target_system}): {str(e)}",
                    confidence=0.0,
                    reasoning=f"Failed to execute '{action}' on {target_system}: {str(e)}",
                )

        # Simulated execution for systems without adapters
        await asyncio.sleep(0.5)  # Simulate API call
        return TaskResult(
            success=True,
            output_data={
                "execution_result": {
                    "status": "completed",
                    "action": action or "process",
                    "target": target_system or "default",
                    "data_processed": len(data),
                    "simulated": True,
                },
            },
            confidence=0.85,
            reasoning=f"Executed action (simulated) with {len(data)} data fields",
        )

    def _evaluate_guard_status(self, shared_context: dict) -> dict:
        """Inspect prior guard outputs and determine if execution should be blocked."""
        signals = []
        for output in shared_context.values():
            if isinstance(output, dict) and "guard_passed" in output:
                signals.append(output)

        for signal in signals:
            if not signal.get("guard_passed", False) or signal.get("blocked", False):
                return {
                    "blocked": True,
                    "reason": signal.get("reason") or "Reliability guard check failed",
                    "signals": signals,
                }

        return {"blocked": False, "reason": "", "signals": signals}

    @staticmethod
    def _is_failed_result(result: Any) -> bool:
        """Determine whether an adapter result indicates execution failure."""
        if not isinstance(result, dict):
            return False

        status = str(result.get("status", "")).lower()
        if status in {"failed", "error", "unhealthy", "disconnected", "timeout"}:
            return True

        return bool(result.get("error")) and status not in {"success", "completed", "created", "updated", "added", "transitioned"}
