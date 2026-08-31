"""Service API Protocol for MCP per-user unified config."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPConfigServiceProtocol(Protocol):
    """Service API for per-user MCP config (get / validate / update / rollback)."""

    def get_user_unified_config(self, *args: Any, **kwargs: Any) -> Any: ...

    def validate_headers_for_mcp(self, *args: Any, **kwargs: Any) -> Any: ...

    def update_user_unified_config(self, *args: Any, **kwargs: Any) -> Any: ...

    def rollback_unified_config(self, *args: Any, **kwargs: Any) -> Any: ...

    def build_mcp_sync_payload(self, *args: Any, **kwargs: Any) -> Any: ...
