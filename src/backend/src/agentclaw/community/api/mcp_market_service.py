"""Service API Protocol for MCP marketplace listings."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPMarketServiceProtocol(Protocol):
    """Service API for MCP marketplace listing + detail + tenant browsing."""

    def get_mcp_list(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_mcp_detail(self, *args: Any, **kwargs: Any) -> Any: ...

    def get_tenant_list(self, *args: Any, **kwargs: Any) -> Any: ...
