"""Core-local ports consumed by the Caller identity domain service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CallerMcpSyncProtocol(Protocol):
    """Synchronize Caller-aware MCP identity to the external authority."""

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


__all__ = ["CallerMcpSyncProtocol"]
