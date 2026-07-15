"""Service API Protocol for MCP detail-sync to devices."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPSyncServiceProtocol(Protocol):
    """Service API for syncing MCP details to bot devices."""

    async def sync_mcp_details(self, *args: Any, **kwargs: Any) -> Any: ...

    async def sync_mcp_detail(self, *args: Any, **kwargs: Any) -> Any: ...

    async def remove_mcp_detail(self, *args: Any, **kwargs: Any) -> Any: ...

    async def refresh_mcp_scope(self, *args: Any, **kwargs: Any) -> Any: ...

    async def sync_mcp_identity_to_agent_principal(
        self, *args: Any, **kwargs: Any
    ) -> Any: ...

    async def sync_mcp_detail_to_all_bots(self, *args: Any, **kwargs: Any) -> Any: ...
