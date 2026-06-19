"""
Sentinel-AI Integration Base.

Abstract interface for all external system adapters.
"""

from abc import ABC, abstractmethod
from typing import Callable

from sentinel_ai.core.tool_registry import ToolCapability


class BaseIntegration(ABC):
    """
    Abstract base for external system integrations.

    All adapters (real and mock) implement this interface.
    """

    def __init__(self, name: str, integration_type: str = "generic"):
        self.name = name
        self.integration_type = integration_type
        self._connected = False

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the external system."""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, action: str, data: dict) -> dict:
        """Execute an action on the external system."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> dict:
        """Check the health of the connection."""
        raise NotImplementedError

    def get_tool_capabilities(self) -> list[tuple[ToolCapability, Callable]]:
        """
        Declare the tools this integration provides with rich metadata.

        Override in subclasses to register tools with the dynamic tool registry.
        Each entry is a (ToolCapability, execute_function) pair.

        Returns:
            List of (ToolCapability, async callable) tuples.
        """
        return []

    def _make_action_executor(self, action: str) -> Callable:
        """
        Create an async executor function bound to a specific action.

        This wraps self.execute(action, data) into a callable compatible
        with the tool registry's execute function signature.
        """
        adapter = self

        async def executor(context: dict) -> dict:
            data = context.get("input_data", {})
            return await adapter.execute(action, data)

        return executor

    async def validate_schema(self, action: str, data: dict) -> bool:
        """Validate data against the expected schema for an action."""
        return True  # Override for strict validation

    async def disconnect(self) -> None:
        """Close the connection."""
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
