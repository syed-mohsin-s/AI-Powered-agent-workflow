"""
Sentinel-AI Dynamic Tool Registry.

A capability-rich registry that stores metadata about every available tool,
enabling semantic search, ranking, and LLM-driven tool selection.

Replaces the simple agent_type → function mapping with a queryable
tool catalog supporting:
- Capability-based search (semantic tag matching)
- Reliability tracking (live score updates from execution outcomes)
- Cost/latency-aware ranking
- Auto-discovery from MCP servers and integration adapters
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sentinel_ai.utils.logger import get_logger

logger = get_logger("core.tool_registry")


@dataclass
class ToolCapability:
    """Rich metadata for a registered tool."""

    name: str
    description: str
    provider: str  # e.g., "erp", "atlassian_mcp", "email"
    category: str  # e.g., "payment", "issue_tracking", "communication"

    # Schema
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)

    # Semantic tags for capability search
    capabilities: list[str] = field(default_factory=list)

    # Operational metadata
    authentication: str = "none"
    estimated_latency_ms: int = 500
    cost_tier: str = "low"  # "free", "low", "medium", "high"
    permissions_required: list[str] = field(default_factory=list)

    # Live metrics (updated from execution outcomes)
    reliability_score: float = 0.8
    total_executions: int = 0
    successful_executions: int = 0
    average_latency_ms: float = 0.0

    # Availability
    is_available: bool = True

    # Arbitrary extra metadata
    metadata: dict = field(default_factory=dict)

    # Timestamps
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: Optional[datetime] = None

    def matches_query(self, query: str) -> float:
        """
        Compute a relevance score for a search query.

        Uses keyword overlap between the query tokens and the tool's
        name, description, category, and capability tags.

        Returns a score in [0.0, 1.0].
        """
        query_tokens = set(query.lower().replace("_", " ").replace("-", " ").split())
        if not query_tokens:
            return 0.0

        # Build a searchable text corpus from tool metadata
        searchable_parts = [
            self.name.lower().replace("_", " "),
            self.description.lower(),
            self.category.lower().replace("_", " "),
            self.provider.lower(),
            " ".join(c.lower() for c in self.capabilities),
        ]
        searchable_text = " ".join(searchable_parts)
        searchable_tokens = set(searchable_text.split())

        # Exact match bonus
        if query.lower().replace(" ", "_") == self.name.lower():
            return 1.0

        # Token overlap scoring
        matched = query_tokens & searchable_tokens
        if not matched:
            # Substring matching as fallback
            score = 0.0
            for qt in query_tokens:
                for st in searchable_tokens:
                    if len(st) >= 3 and len(qt) >= 3:
                        if qt in st or st in qt:
                            score += 0.3
            return min(score / len(query_tokens), 0.6)

        return len(matched) / len(query_tokens)

    def to_summary(self) -> dict:
        """Return a concise summary for LLM consumption."""
        return {
            "name": self.name,
            "description": self.description,
            "provider": self.provider,
            "category": self.category,
            "capabilities": self.capabilities,
            "input_schema": self.input_schema,
            "estimated_latency_ms": self.estimated_latency_ms,
            "cost_tier": self.cost_tier,
            "reliability_score": round(self.reliability_score, 3),
            "is_available": self.is_available,
        }


class ToolRegistry:
    """
    Dynamic tool registry with capability-based search and ranking.

    Stores tool metadata alongside their execute functions, enabling
    agents to discover, compare, and select the best tool for a task.
    """

    def __init__(self):
        self._tools: dict[str, ToolCapability] = {}
        self._execute_fns: dict[str, Callable] = {}
        self._category_index: dict[str, set[str]] = {}  # category → tool names
        self._provider_index: dict[str, set[str]] = {}  # provider → tool names

    # -------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------

    def register_tool(
        self,
        capability: ToolCapability,
        execute_fn: Callable,
    ) -> None:
        """Register a tool with its capability metadata and execute function."""
        self._tools[capability.name] = capability
        self._execute_fns[capability.name] = execute_fn

        # Update indexes
        cat = capability.category.lower()
        self._category_index.setdefault(cat, set()).add(capability.name)

        prov = capability.provider.lower()
        self._provider_index.setdefault(prov, set()).add(capability.name)

        logger.info(
            f"Tool registered: {capability.name} "
            f"(provider={capability.provider}, category={capability.category})",
        )

    def register_from_agent(
        self,
        agent_type: str,
        execute_fn: Callable,
        description: str = "",
    ) -> None:
        """
        Backward-compatible registration from the existing agent registry.

        Creates a minimal ToolCapability from an agent_type string.
        """
        capability = ToolCapability(
            name=f"agent:{agent_type}",
            description=description or f"Agent of type '{agent_type}'",
            provider="sentinel_ai",
            category="agent",
            capabilities=[agent_type],
            metadata={"legacy_agent": True},
        )
        self.register_tool(capability, execute_fn)

    def unregister_tool(self, name: str) -> None:
        """Remove a tool from the registry."""
        cap = self._tools.pop(name, None)
        self._execute_fns.pop(name, None)
        if cap:
            cat_set = self._category_index.get(cap.category.lower())
            if cat_set:
                cat_set.discard(name)
            prov_set = self._provider_index.get(cap.provider.lower())
            if prov_set:
                prov_set.discard(name)
            logger.info(f"Tool unregistered: {name}")

    # -------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------

    def get_all_tools(self) -> list[ToolCapability]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_available_tools(self) -> list[ToolCapability]:
        """Return only currently available tools."""
        return [t for t in self._tools.values() if t.is_available]

    def get_tool(self, name: str) -> Optional[ToolCapability]:
        """Get a tool by exact name."""
        return self._tools.get(name)

    def get_execute_fn(self, name: str) -> Optional[Callable]:
        """Get the execute function for a tool."""
        return self._execute_fns.get(name)

    def get_tools_by_provider(self, provider: str) -> list[ToolCapability]:
        """Get all tools from a specific provider."""
        names = self._provider_index.get(provider.lower(), set())
        return [self._tools[n] for n in names if n in self._tools]

    def get_tools_by_category(self, category: str) -> list[ToolCapability]:
        """Get all tools in a specific category."""
        names = self._category_index.get(category.lower(), set())
        return [self._tools[n] for n in names if n in self._tools]

    # -------------------------------------------------------------------
    # Search & Ranking
    # -------------------------------------------------------------------

    def search_tools(
        self,
        query: str,
        category: Optional[str] = None,
        provider: Optional[str] = None,
        min_reliability: float = 0.0,
        only_available: bool = True,
        exclude: Optional[list[str]] = None,
    ) -> list[ToolCapability]:
        """
        Search for tools matching a query with optional filters.

        Returns tools sorted by relevance score (descending).
        """
        candidates = (
            self.get_available_tools() if only_available else list(self._tools.values())
        )

        # Apply filters
        if category:
            cat_lower = category.lower()
            candidates = [t for t in candidates if t.category.lower() == cat_lower]

        if provider:
            prov_lower = provider.lower()
            candidates = [t for t in candidates if t.provider.lower() == prov_lower]

        if min_reliability > 0:
            candidates = [
                t for t in candidates if t.reliability_score >= min_reliability
            ]

        if exclude:
            exclude_set = set(exclude)
            candidates = [t for t in candidates if t.name not in exclude_set]

        # Score and sort
        scored = []
        for tool in candidates:
            score = tool.matches_query(query)
            if score > 0:
                scored.append((score, tool))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [tool for _, tool in scored]

    def get_tools_for_action(
        self,
        action: str,
        target_system: Optional[str] = None,
    ) -> list[ToolCapability]:
        """
        Find tools that can perform a specific action.

        This is the primary lookup used by the execution agent.
        If target_system is provided, it biases results toward that provider.
        """
        candidates = self.search_tools(query=action, only_available=True)

        if target_system:
            # Boost tools from the target provider
            provider_tools = []
            other_tools = []
            for tool in candidates:
                if tool.provider.lower() == target_system.lower():
                    provider_tools.append(tool)
                else:
                    other_tools.append(tool)
            return provider_tools + other_tools

        return candidates

    def rank_tools(
        self,
        candidates: list[ToolCapability],
        context: Optional[dict] = None,
        query: Optional[str] = None,
    ) -> list[ToolCapability]:
        """
        Rank candidate tools by composite score.

        Scoring factors:
        - Relevance score (weight: 0.5)
        - Reliability score (weight: 0.2)
        - Latency (lower is better, weight: 0.1)
        - Cost tier (lower is better, weight: 0.1)
        - Recency of use (weight: 0.1)
        """
        cost_scores = {"free": 1.0, "low": 0.8, "medium": 0.5, "high": 0.2}

        def composite_score(tool: ToolCapability) -> float:
            relevance = tool.matches_query(query) if query else 1.0
            relevance_score = relevance * 0.5

            reliability = tool.reliability_score * 0.2

            # Normalize latency: 0-100ms → 1.0, 5000ms+ → 0.0
            latency_norm = max(0.0, 1.0 - tool.estimated_latency_ms / 5000)
            latency = latency_norm * 0.1

            cost = cost_scores.get(tool.cost_tier, 0.5) * 0.1

            # Recency bonus: recently used tools get a small boost
            recency = 0.0
            if tool.last_used_at:
                age_seconds = (
                    datetime.now(timezone.utc) - tool.last_used_at
                ).total_seconds()
                recency = max(0.0, 1.0 - age_seconds / 86400) * 0.1  # 24h decay
            else:
                recency = 0.05  # Small default for unused tools

            return relevance_score + reliability + latency + cost + recency

        return sorted(candidates, key=composite_score, reverse=True)

    # -------------------------------------------------------------------
    # Reliability Tracking
    # -------------------------------------------------------------------

    def update_reliability(
        self,
        tool_name: str,
        success: bool,
        latency_ms: float = 0.0,
    ) -> None:
        """
        Update a tool's reliability score and latency from execution outcome.

        Uses exponential moving average for smooth updates.
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return

        tool.total_executions += 1
        if success:
            tool.successful_executions += 1
        tool.last_used_at = datetime.now(timezone.utc)

        # Exponential moving average for reliability
        alpha = 0.1  # Learning rate
        outcome = 1.0 if success else 0.0
        tool.reliability_score = (1 - alpha) * tool.reliability_score + alpha * outcome

        # Update average latency
        if latency_ms > 0:
            if tool.average_latency_ms == 0:
                tool.average_latency_ms = latency_ms
            else:
                tool.average_latency_ms = (
                    1 - alpha
                ) * tool.average_latency_ms + alpha * latency_ms

    # -------------------------------------------------------------------
    # MCP Discovery
    # -------------------------------------------------------------------

    async def discover_from_mcp(self, mcp_adapter: Any) -> list[ToolCapability]:
        """
        Auto-discover tools from an MCP server.

        Calls list_tools() on the MCP adapter and converts each
        MCP tool into a ToolCapability with a wrapped execute function.
        """
        try:
            result = await mcp_adapter.execute("list_tools", {})
            if result.get("status") != "success":
                logger.warning(f"MCP tool discovery failed: {result}")
                return []

            discovered = []
            for tool_info in result.get("tools", []):
                tool_name = tool_info.get("name", "")
                if not tool_name:
                    continue

                # Skip if already registered
                if tool_name in self._tools:
                    continue

                description = tool_info.get("description", "")
                capability = ToolCapability(
                    name=tool_name,
                    description=description,
                    provider=mcp_adapter.name.lower().replace(" ", "_"),
                    category=self._infer_category_from_description(
                        description, tool_name
                    ),
                    capabilities=self._extract_capability_tags(tool_name, description),
                    input_schema=tool_info.get("inputSchema", {}),
                    authentication="configured",
                    estimated_latency_ms=1000,  # MCP tools tend to be slower
                    cost_tier="low",
                    metadata={"source": "mcp_discovery", "raw": tool_info},
                )

                # Create a wrapped execute function for this MCP tool
                adapter_ref = mcp_adapter

                async def make_mcp_executor(tn: str, adapter: Any):
                    async def mcp_execute(context: dict) -> dict:
                        data = context.get("input_data", {})
                        return await adapter.execute(
                            "call_tool",
                            {"tool_name": tn, "arguments": data},
                        )

                    return mcp_execute

                execute_fn = await make_mcp_executor(tool_name, adapter_ref)
                self.register_tool(capability, execute_fn)
                discovered.append(capability)

            logger.info(
                f"Discovered {len(discovered)} tools from MCP: {mcp_adapter.name}"
            )
            return discovered

        except Exception as e:
            logger.error(f"MCP discovery error: {e}", exc_info=True)
            return []

    async def discover_from_integration(self, adapter: Any) -> list[ToolCapability]:
        """
        Discover tools from any integration adapter that implements
        get_tool_capabilities().
        """
        if not hasattr(adapter, "get_tool_capabilities"):
            return []

        try:
            capabilities = adapter.get_tool_capabilities()
            discovered = []
            for cap, execute_fn in capabilities:
                if cap.name not in self._tools:
                    self.register_tool(cap, execute_fn)
                    discovered.append(cap)

            logger.info(
                f"Discovered {len(discovered)} tools from integration: {adapter.name}"
            )
            return discovered

        except Exception as e:
            logger.error(f"Integration discovery error for {adapter.name}: {e}")
            return []

    # -------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------

    def get_registry_summary(self) -> dict:
        """Get a summary of the registry for API/dashboard consumption."""
        by_provider: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for tool in self._tools.values():
            by_provider[tool.provider] = by_provider.get(tool.provider, 0) + 1
            by_category[tool.category] = by_category.get(tool.category, 0) + 1

        return {
            "total_tools": len(self._tools),
            "available_tools": sum(1 for t in self._tools.values() if t.is_available),
            "by_provider": by_provider,
            "by_category": by_category,
            "tools": [t.to_summary() for t in self._tools.values()],
        }

    def get_tools_for_llm_prompt(
        self,
        candidates: Optional[list[ToolCapability]] = None,
    ) -> str:
        """
        Format tool descriptions for inclusion in an LLM prompt.

        Returns a numbered list of tools with their key metadata.
        """
        tools = candidates or self.get_available_tools()
        if not tools:
            return "No tools available."

        lines = []
        for i, tool in enumerate(tools, 1):
            lines.append(
                f"{i}. **{tool.name}** — {tool.description}\n"
                f"   Provider: {tool.provider} | Category: {tool.category}\n"
                f"   Latency: ~{tool.estimated_latency_ms}ms | "
                f"Cost: {tool.cost_tier} | "
                f"Reliability: {tool.reliability_score:.0%}\n"
                f"   Input: {tool.input_schema or 'flexible'}"
            )
        return "\n\n".join(lines)

    # -------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _infer_category_from_description(description: str, name: str) -> str:
        """Infer a tool category from its description and name."""
        text = f"{name} {description}".lower()
        category_keywords = {
            "issue_tracking": ["issue", "ticket", "jira", "bug", "task"],
            "payment": ["payment", "pay", "invoice", "billing"],
            "communication": ["email", "message", "notify", "slack", "send"],
            "document": ["document", "file", "report", "pdf"],
            "search": ["search", "find", "query", "lookup"],
            "provisioning": ["provision", "account", "access", "create user"],
            "monitoring": ["monitor", "health", "status", "check"],
        }
        for category, keywords in category_keywords.items():
            if any(kw in text for kw in keywords):
                return category
        return "general"

    @staticmethod
    def _extract_capability_tags(name: str, description: str) -> list[str]:
        """Extract semantic capability tags from tool name and description."""
        text = f"{name} {description}".lower()
        # Split on common separators
        tokens = set()
        for sep in ["_", "-", " ", ".", "/"]:
            tokens.update(text.split(sep))
        # Remove very short or stop-word tokens
        stop_words = {
            "a",
            "an",
            "the",
            "to",
            "for",
            "of",
            "in",
            "on",
            "is",
            "and",
            "or",
            "with",
        }
        return [t.strip() for t in tokens if len(t) > 2 and t not in stop_words]


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get the global tool registry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
