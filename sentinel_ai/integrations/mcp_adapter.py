"""
Sentinel-AI MCP Integration Adapter.

Uses standard Model Context Protocol (MCP) to interact dynamically
with external tools via stdio processes.
"""

import os
from contextlib import AsyncExitStack
from typing import Any, Callable, List, Optional

from sentinel_ai.core.tool_registry import ToolCapability
from sentinel_ai.integrations.base import BaseIntegration
from sentinel_ai.utils.logger import get_logger

logger = get_logger("integrations.mcp")

try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


class MCPAdapter(BaseIntegration):
    """
    Model Context Protocol (MCP) Client Integration.

    Spins up and connects to an MCP Server via stdio per-request
    to prevent AsyncIO cross-task context enforcement issues.
    """

    def __init__(
        self, name: str = "Atlassian MCP", command: str = "npx", args: List[str] = None
    ):
        super().__init__(name=name, integration_type="mcp_client")
        self.command = command
        self.args = args or ["-y", "@modelcontextprotocol/server-atlassian"]
        self._default_tool_map = {
            "create_issue": [
                "jira_create_issue",
                "create_jira_issue",
                "createIssue",
                "create_issue",
            ],
            "update_issue": [
                "jira_update_issue",
                "update_jira_issue",
                "updateIssue",
                "update_issue",
            ],
            "add_comment": [
                "jira_add_comment",
                "add_jira_comment",
                "addComment",
                "add_comment",
            ],
            "search": ["jira_search", "search_jira", "searchIssues", "search"],
            "get_issue": ["jira_get_issue", "get_jira_issue", "getIssue", "get_issue"],
            "transition": [
                "jira_transition_issue",
                "transition_jira_issue",
                "transitionIssue",
                "transition",
            ],
        }

    async def connect(self) -> bool:
        """Verifies the integration is available."""
        if not MCP_AVAILABLE:
            logger.warning("MCP libraries not installed. Run `pip install mcp`.")
            self._connected = False
            return False

        self._connected = True
        return True

    async def execute(self, action: str, data: dict) -> dict:
        """Execute an action (tool) on the MCP Server by initializing dynamically."""
        if not self._connected:
            return await self._mock_execute(action, data)

        env = os.environ.copy()
        server_params = StdioServerParameters(
            command=self.command, args=self.args, env=env
        )

        try:
            async with AsyncExitStack() as stack:
                stdio = await stack.enter_async_context(stdio_client(server_params))
                read, write = stdio
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()

                if action == "call_tool":
                    tool_name = data.get("tool_name")
                    tool_args = data.get("arguments", {})
                    logger.info(f"Calling MCP Tool: {tool_name}")
                    result = await session.call_tool(tool_name, tool_args)

                    output = [c.text for c in result.content if c.type == "text"]
                    return {"status": "success", "result": output}

                elif action == "list_tools":
                    tools_response = await session.list_tools()
                    tools_list = [
                        {"name": t.name, "description": t.description}
                        for t in tools_response.tools
                    ]
                    return {"status": "success", "tools": tools_list}

                elif action in self._default_tool_map:
                    tool_name = await self._resolve_tool_name(session, action)
                    if not tool_name:
                        tools_response = await session.list_tools()
                        available_tools = [t.name for t in tools_response.tools]
                        return {
                            "status": "failed",
                            "error": f"No Atlassian MCP tool found for action '{action}'",
                            "available_tools": available_tools,
                        }

                    logger.info(f"Mapping action '{action}' to MCP tool '{tool_name}'")
                    result = await session.call_tool(tool_name, data)
                    output = [c.text for c in result.content if c.type == "text"]
                    return {
                        "status": "success",
                        "action": action,
                        "tool": tool_name,
                        "result": output,
                    }

                else:
                    return {
                        "error": f"Unknown MCP action: {action}",
                        "status": "failed",
                    }

        except Exception as e:
            logger.error(f"MCP Action failed: {e}", exc_info=True)
            return await self._mock_execute(action, data, error=str(e))

    async def _resolve_tool_name(self, session: Any, action: str) -> Optional[str]:
        """Resolve the best matching MCP tool name for a high-level action."""
        tools_response = await session.list_tools()
        available_names = [t.name for t in tools_response.tools]
        lowered_map = {name.lower(): name for name in available_names}

        for candidate in self._default_tool_map.get(action, []):
            if candidate.lower() in lowered_map:
                return lowered_map[candidate.lower()]

        heuristics = {
            "create_issue": ["jira", "create", "issue"],
            "update_issue": ["jira", "update", "issue"],
            "add_comment": ["jira", "comment"],
            "search": ["jira", "search"],
            "get_issue": ["jira", "get", "issue"],
            "transition": ["jira", "transition"],
        }

        tokens = heuristics.get(action, [])
        for tool in available_names:
            tool_lower = tool.lower()
            if all(token in tool_lower for token in tokens):
                return tool

        return None

    async def _mock_execute(
        self, action: str, data: dict, error: Optional[str] = None
    ) -> dict:
        """Mock execution when MCP is unavailable or a tool call fails."""
        if action == "list_tools":
            response = {
                "status": "success",
                "tools": [
                    {"name": "jira_create_issue", "description": "Mock Atlassian tool"}
                ],
                "simulated": True,
            }
            if error:
                response["fallback_error"] = error
            return response

        if action in self._default_tool_map or action == "call_tool":
            response = {
                "status": "success",
                "action": action,
                "result": ["Simulated Atlassian MCP response"],
                "simulated": True,
            }
            if error:
                response["fallback_error"] = error
            return response

        return {
            "error": f"Unknown MCP action: {action}",
            "status": "failed",
            "simulated": True,
        }

    async def close(self):
        """Shutdown."""
        self._connected = False

    async def health_check(self) -> dict:
        return {
            "status": "healthy" if self._connected else "unhealthy",
            "connected": self._connected,
        }

    def get_tool_capabilities(self) -> list[tuple[ToolCapability, Callable]]:
        """
        Declare known MCP tool capabilities.

        These are fallback entries for the well-known Atlassian actions.
        Dynamic discovery via the tool registry's discover_from_mcp()
        will add more tools at runtime when the MCP server is reachable.
        """
        provider = self.name.lower().replace(" ", "_")
        tools = [
            (
                ToolCapability(
                    name="mcp_jira_create_issue",
                    description="Create a new issue in Jira via the Atlassian MCP server. Supports all standard Jira fields.",
                    provider=provider,
                    category="issue_tracking",
                    capabilities=[
                        "create",
                        "issue",
                        "jira",
                        "atlassian",
                        "ticket",
                        "mcp",
                    ],
                    input_schema={
                        "required": ["project", "summary"],
                        "optional": [
                            "description",
                            "issue_type",
                            "priority",
                            "assignee",
                            "labels",
                        ],
                    },
                    output_schema={"issue_key": "str", "status": "str"},
                    authentication="configured",
                    estimated_latency_ms=1000,
                    cost_tier="low",
                    permissions_required=["write:jira"],
                ),
                self._make_mcp_tool_executor("create_issue"),
            ),
            (
                ToolCapability(
                    name="mcp_jira_update_issue",
                    description="Update an existing Jira issue with new fields, status changes, or assignments.",
                    provider=provider,
                    category="issue_tracking",
                    capabilities=[
                        "update",
                        "issue",
                        "jira",
                        "atlassian",
                        "modify",
                        "mcp",
                    ],
                    input_schema={
                        "required": ["issue_key"],
                        "optional": [
                            "summary",
                            "description",
                            "status",
                            "assignee",
                            "priority",
                        ],
                    },
                    output_schema={"issue_key": "str", "status": "str"},
                    authentication="configured",
                    estimated_latency_ms=1000,
                    cost_tier="low",
                    permissions_required=["write:jira"],
                ),
                self._make_mcp_tool_executor("update_issue"),
            ),
            (
                ToolCapability(
                    name="mcp_jira_add_comment",
                    description="Add a comment to an existing Jira issue.",
                    provider=provider,
                    category="issue_tracking",
                    capabilities=["comment", "issue", "jira", "atlassian", "mcp"],
                    input_schema={
                        "required": ["issue_key", "body"],
                        "optional": [],
                    },
                    output_schema={"status": "str"},
                    authentication="configured",
                    estimated_latency_ms=800,
                    cost_tier="free",
                    permissions_required=["write:jira"],
                ),
                self._make_mcp_tool_executor("add_comment"),
            ),
            (
                ToolCapability(
                    name="mcp_jira_search",
                    description="Search Jira issues using JQL or keyword queries.",
                    provider=provider,
                    category="search",
                    capabilities=[
                        "search",
                        "query",
                        "jira",
                        "atlassian",
                        "find",
                        "mcp",
                    ],
                    input_schema={
                        "required": ["query"],
                        "optional": ["max_results", "fields"],
                    },
                    output_schema={"issues": "list", "total": "int"},
                    authentication="configured",
                    estimated_latency_ms=800,
                    cost_tier="free",
                    permissions_required=["read:jira"],
                ),
                self._make_mcp_tool_executor("search"),
            ),
            (
                ToolCapability(
                    name="mcp_jira_transition",
                    description="Transition a Jira issue to a new status (e.g., In Progress, Done, Closed).",
                    provider=provider,
                    category="issue_tracking",
                    capabilities=[
                        "transition",
                        "status",
                        "jira",
                        "atlassian",
                        "workflow",
                        "mcp",
                    ],
                    input_schema={
                        "required": ["issue_key", "transition"],
                        "optional": ["comment"],
                    },
                    output_schema={"issue_key": "str", "status": "str"},
                    authentication="configured",
                    estimated_latency_ms=1000,
                    cost_tier="low",
                    permissions_required=["write:jira"],
                ),
                self._make_mcp_tool_executor("transition"),
            ),
        ]
        return tools

    def _make_mcp_tool_executor(self, action: str) -> Callable:
        """Create an executor that routes through the MCP adapter's execute method."""
        adapter = self

        async def executor(context: dict) -> dict:
            data = context.get("input_data", {})
            return await adapter.execute(action, data)

        return executor
