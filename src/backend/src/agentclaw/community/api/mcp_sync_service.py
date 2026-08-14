"""Service API Protocol for MCP detail-sync to devices."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPSyncServiceProtocol(Protocol):
    """Service API for syncing MCP details to bot devices."""

    async def sync_mcp_details(self, *args: Any, **kwargs: Any) -> Any: ...

    async def sync_mcp_detail(self, *args: Any, **kwargs: Any) -> Any: ...

    async def remove_mcp_detail(self, *args: Any, **kwargs: Any) -> Any: ...

    async def refresh_mcp_scope(self, *args: Any, **kwargs: Any) -> Any: ...

    async def sync_mcp_identity_to_agent_principal(
        self,
        *,
        user_id: str,
        entity_id: str,
        bot_id: str,
        entity_type: str,
        engine_type: str,
        active_mcps: list[dict[str, Any]],
        identity_modes: Mapping[str, object],
    ) -> Mapping[str, Any]: ...

    async def sync_mcp_detail_to_all_bots(self, *args: Any, **kwargs: Any) -> Any: ...
