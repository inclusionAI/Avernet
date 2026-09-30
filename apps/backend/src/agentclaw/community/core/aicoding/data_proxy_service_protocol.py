"""Service API Protocol for the aicoding data-proxy."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DataProxyServiceProtocol(Protocol):
    """Service API for forwarding ``/api/aicoding/data-proxy/*`` requests to the engine ``/data/*`` leg."""

    async def forward(self, *args: Any, **kwargs: Any) -> Any: ...

    def resolve_engine_base(self, *args: Any, **kwargs: Any) -> Any: ...
