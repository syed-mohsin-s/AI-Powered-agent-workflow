"""
Sentinel-AI Execution Agent.

Performs actions via integration adapters (ERP, Atlassian MCP, Email, ServiceNow).
Supports LLM-driven tool selection when multiple tools are available for an action,
with fallback to direct dispatch when the tool is unambiguous.
"""

import asyncio
import json
import time
from typing import Any

from sentinel_ai.agents.base import BaseAgent
from sentinel_ai.core.tool_registry import ToolCapability, get_tool_registry
from sentinel_ai.models.workflow import TaskResult
from sentinel_ai.utils.logger import get_logger

logger = get_logger("agents.execution")


class ExecutionAgent(BaseAgent):
    """
    Executes external actions via integration adapters.

    Responsibilities:
    - Discover available tools from the registry for a given action
    - Use LLM to select the best tool when multiple candidates exist
    - Validate arguments against tool schemas
    - Execute via appropriate adapter
    - Track tool reliability from outcomes
    - Handle external system errors
    - Support rollback on failure

    Backward compatible: workflows that specify action + target_system
    still work — target_system becomes a hint that biases tool selection.
    """

    def __init__(self):
        super().__init__(
            name="Execution Agent",
            agent_type="execution",
        )
        self._integrations: dict[str, Any] = {}
        self._tool_registry = get_tool_registry()

    def register_integration(self, name: str, adapter: Any) -> None:
        """Register an integration adapter."""
        self._integrations[name] = adapter

    async def execute(self, context: dict) -> TaskResult:
        """Execute an action using intelligent tool selection."""
        input_data = context.get("input_data", {})
        shared_context = context.get("shared_context", {})
        action = input_data.get("action", "")
        target_system = input_data.get("target_system", "")
        task_name = context.get("task_name", action)

        # --- Guard check ---
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

        # --- Merge context data ---
        data = {}
        for output in shared_context.values():
            if isinstance(output, dict):
                merged = output.get("merged_data", output.get("extracted", output))
                if isinstance(merged, dict):
                    data.update(merged)
        data.update(input_data)

        # --- Check if decision was to reject ---
        for output in shared_context.values():
            if isinstance(output, dict) and output.get("decision") == "reject":
                return TaskResult(
                    success=True,
                    output_data={
                        "action": "skipped",
                        "reason": "Decision was to reject",
                    },
                    confidence=1.0,
                    reasoning="Action skipped — decision agent rejected the request",
                )

        # --- Tool selection ---
        reasoning_trace = []

        # 1. Discover candidate tools from the registry
        candidates = self._tool_registry.get_tools_for_action(
            action=action,
            target_system=target_system or None,
        )
        tools_considered = [c.name for c in candidates]

        reasoning_trace.append(
            {
                "step": "discovery",
                "action": action,
                "target_system": target_system,
                "candidates_found": len(candidates),
                "candidates": tools_considered,
            }
        )

        selected_tool = None
        tool_execute_fn = None
        validated_args = data

        if candidates:
            if len(candidates) == 1:
                # Single candidate — skip LLM, use directly
                selected_tool = candidates[0]
                tool_execute_fn = self._tool_registry.get_execute_fn(selected_tool.name)
                reasoning_trace.append(
                    {
                        "step": "selection",
                        "method": "single_candidate",
                        "selected": selected_tool.name,
                    }
                )
            elif self._llm.is_available and len(candidates) > 1:
                # Multiple candidates — ask LLM to choose
                try:
                    selection = await self._llm_select_tool(
                        goal=task_name,
                        action=action,
                        candidates=candidates,
                        context=shared_context,
                        available_data=data,
                    )
                    selected_name = selection.get("tool_name", "")
                    selected_tool = self._tool_registry.get_tool(selected_name)

                    if selected_tool:
                        tool_execute_fn = self._tool_registry.get_execute_fn(
                            selected_name
                        )
                        # Use LLM-validated arguments if provided
                        if selection.get("arguments"):
                            validated_args = {**data, **selection["arguments"]}
                        reasoning_trace.append(
                            {
                                "step": "selection",
                                "method": "llm",
                                "selected": selected_name,
                                "llm_reasoning": selection.get("reasoning", ""),
                            }
                        )
                    else:
                        # LLM picked an unknown tool — fall back to best ranked
                        ranked = self._tool_registry.rank_tools(candidates)
                        selected_tool = ranked[0]
                        tool_execute_fn = self._tool_registry.get_execute_fn(
                            selected_tool.name
                        )
                        reasoning_trace.append(
                            {
                                "step": "selection",
                                "method": "llm_fallback_to_ranking",
                                "llm_suggested": selected_name,
                                "selected": selected_tool.name,
                            }
                        )
                except Exception as e:
                    logger.warning(
                        f"LLM tool selection failed, falling back to ranking: {e}"
                    )
                    ranked = self._tool_registry.rank_tools(candidates)
                    selected_tool = ranked[0]
                    tool_execute_fn = self._tool_registry.get_execute_fn(
                        selected_tool.name
                    )
                    reasoning_trace.append(
                        {
                            "step": "selection",
                            "method": "ranking_fallback",
                            "error": str(e),
                            "selected": selected_tool.name,
                        }
                    )
            else:
                # Multiple candidates but no LLM — rank and pick best
                ranked = self._tool_registry.rank_tools(candidates)
                selected_tool = ranked[0]
                tool_execute_fn = self._tool_registry.get_execute_fn(selected_tool.name)
                reasoning_trace.append(
                    {
                        "step": "selection",
                        "method": "ranking",
                        "selected": selected_tool.name,
                    }
                )

        # --- Execute via selected tool ---
        if selected_tool and tool_execute_fn:
            return await self._execute_via_tool(
                tool=selected_tool,
                execute_fn=tool_execute_fn,
                context=context,
                data=validated_args,
                tools_considered=tools_considered,
                reasoning_trace=reasoning_trace,
            )

        # --- Fallback: legacy adapter dispatch ---
        reasoning_trace.append(
            {
                "step": "fallback",
                "method": "legacy_adapter",
                "target_system": target_system,
            }
        )

        adapter = self._integrations.get(target_system)
        if adapter:
            return await self._execute_via_adapter(
                adapter=adapter,
                action=action,
                target_system=target_system,
                data=data,
                tools_considered=tools_considered,
                reasoning_trace=reasoning_trace,
            )

        # --- Simulated execution ---
        await asyncio.sleep(0.5)
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
            tools_considered=tools_considered,
            reasoning_trace=reasoning_trace,
        )

    # -------------------------------------------------------------------
    # Tool Execution
    # -------------------------------------------------------------------

    async def _execute_via_tool(
        self,
        tool: ToolCapability,
        execute_fn,
        context: dict,
        data: dict,
        tools_considered: list[str],
        reasoning_trace: list[dict],
    ) -> TaskResult:
        """Execute an action via a tool from the registry."""
        start = time.time()
        try:
            # Build context for the tool executor
            tool_context = {
                "workflow_id": context.get("workflow_id"),
                "task_id": context.get("task_id"),
                "input_data": data,
                "shared_context": context.get("shared_context", {}),
            }
            result = await execute_fn(tool_context)
            latency_ms = (time.time() - start) * 1000

            # Update tool reliability
            success = True
            if isinstance(result, dict):
                success = not self._is_failed_result(result)
            elif isinstance(result, TaskResult):
                success = result.success

            self._tool_registry.update_reliability(tool.name, success, latency_ms)

            reasoning_trace.append(
                {
                    "step": "execution",
                    "tool": tool.name,
                    "provider": tool.provider,
                    "success": success,
                    "latency_ms": round(latency_ms, 1),
                }
            )

            if isinstance(result, TaskResult):
                result.tool_used = tool.name
                result.tools_considered = tools_considered
                result.reasoning_trace = reasoning_trace
                return result

            if self._is_failed_result(result):
                return TaskResult(
                    success=False,
                    error_message=f"Tool '{tool.name}' returned failure",
                    output_data={"execution_result": result, "tool": tool.name},
                    confidence=0.2,
                    reasoning=f"Tool '{tool.name}' (provider: {tool.provider}) returned failure status",
                    tool_used=tool.name,
                    tools_considered=tools_considered,
                    reasoning_trace=reasoning_trace,
                )

            return TaskResult(
                success=True,
                output_data={
                    "execution_result": result,
                    "tool": tool.name,
                    "provider": tool.provider,
                },
                confidence=0.9,
                reasoning=f"Successfully executed via tool '{tool.name}' (provider: {tool.provider})",
                tool_used=tool.name,
                tools_considered=tools_considered,
                reasoning_trace=reasoning_trace,
            )

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            self._tool_registry.update_reliability(tool.name, False, latency_ms)
            reasoning_trace.append(
                {
                    "step": "execution_error",
                    "tool": tool.name,
                    "error": str(e),
                    "latency_ms": round(latency_ms, 1),
                }
            )
            return TaskResult(
                success=False,
                error_message=f"Tool '{tool.name}' error: {str(e)}",
                confidence=0.0,
                reasoning=f"Failed to execute via tool '{tool.name}': {str(e)}",
                tool_used=tool.name,
                tools_considered=tools_considered,
                reasoning_trace=reasoning_trace,
            )

    async def _execute_via_adapter(
        self,
        adapter,
        action: str,
        target_system: str,
        data: dict,
        tools_considered: list[str],
        reasoning_trace: list[dict],
    ) -> TaskResult:
        """Legacy execution path via direct adapter dispatch."""
        try:
            result = await adapter.execute(action, data)
            if self._is_failed_result(result):
                return TaskResult(
                    success=False,
                    error_message=f"Integration action failed on {target_system}",
                    output_data={
                        "execution_result": result,
                        "system": target_system,
                        "action": action,
                    },
                    confidence=0.2,
                    reasoning=f"Adapter returned failure status for '{action}' on {target_system}",
                    tools_considered=tools_considered,
                    reasoning_trace=reasoning_trace,
                )

            return TaskResult(
                success=True,
                output_data={
                    "execution_result": result,
                    "system": target_system,
                    "action": action,
                },
                confidence=0.9,
                reasoning=f"Successfully executed '{action}' on {target_system} (legacy adapter path)",
                tools_considered=tools_considered,
                reasoning_trace=reasoning_trace,
            )
        except Exception as e:
            return TaskResult(
                success=False,
                error_message=f"Integration error ({target_system}): {str(e)}",
                confidence=0.0,
                reasoning=f"Failed to execute '{action}' on {target_system}: {str(e)}",
                tools_considered=tools_considered,
                reasoning_trace=reasoning_trace,
            )

    # -------------------------------------------------------------------
    # LLM-Driven Tool Selection
    # -------------------------------------------------------------------

    async def _llm_select_tool(
        self,
        goal: str,
        action: str,
        candidates: list[ToolCapability],
        context: dict,
        available_data: dict,
    ) -> dict:
        """
        Ask the LLM to select the best tool from candidates.

        Returns dict with: tool_name, arguments (optional), reasoning.
        """
        tools_description = self._tool_registry.get_tools_for_llm_prompt(candidates)

        # Summarize available data (truncate for prompt size)
        data_summary = json.dumps(
            {k: v for k, v in available_data.items() if k not in ("content",)},
            indent=2,
            default=str,
        )[:1500]

        prompt = f"""You are an execution agent. Your goal is: {goal}
Action requested: {action}

Available tools:
{tools_description}

Available data from prior tasks:
{data_summary}

Select the best tool for this action. Consider:
1. Tool capabilities and what it does
2. Reliability score (higher is better)
3. Cost tier (lower is better for equivalent tools)
4. Whether the available data matches the tool's input schema

Return ONLY valid JSON with:
{{"tool_name": "<exact tool name>", "arguments": {{}}, "reasoning": "<why this tool>"}}"""

        response = await self.llm_analyze(prompt)

        # Parse LLM response
        try:
            # Handle potential markdown code blocks
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1])
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            import re

            match = re.search(r'\{[^{}]*"tool_name"[^{}]*\}', response, re.DOTALL)
            if match:
                return json.loads(match.group())
            # Default to first candidate
            return {
                "tool_name": candidates[0].name,
                "reasoning": "JSON parse failed, defaulting to top candidate",
            }

    # -------------------------------------------------------------------
    # Guard & Validation
    # -------------------------------------------------------------------

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

        return bool(result.get("error")) and status not in {
            "success",
            "completed",
            "created",
            "updated",
            "added",
            "transitioned",
        }
