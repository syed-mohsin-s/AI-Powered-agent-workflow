"""
Sentinel-AI Integration Base.

Abstract interface for all external system adapters.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


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

    async def validate_schema(self, action: str, data: dict) -> bool:
        """Validate data against the expected schema for an action."""
        return True  # Override for strict validation

    async def disconnect(self) -> None:
        """Close the connection."""
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
