"""Service API Protocol for bot data initialization."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DataInitServiceProtocol(Protocol):
    """Service API for seeding initial bot data (resources, skills)."""

    async def trigger_init(self, *args: Any, **kwargs: Any) -> Any: ...
