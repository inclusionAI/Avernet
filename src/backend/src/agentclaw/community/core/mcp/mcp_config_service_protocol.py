"""Service API for user defaults plus Bot-scoped MCP effective config."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPConfigServiceProtocol(Protocol):
    """Resolve and validate MCP config across user, Bot, and Center scopes."""

    def get_user_unified_config(
        self, user_id: str, server_code: str
    ) -> dict[str, Any] | None: ...

    def get_bot_override(
        self, *, bot_id: str, owner_id: str, server_code: str
    ) -> dict[str, Any] | None:
        """Return only fields explicitly declared for this owner-qualified Bot."""
        ...

    def validate_headers_for_mcp(
        self, server_code: str, headers: dict[str, str]
    ) -> dict[str, Any]: ...

    def validate_bot_override(
        self,
        *,
        user_id: str,
        server_code: str,
        config: dict[str, Any] | None,
        engine_type: str | None = None,
    ) -> dict[str, Any]:
        """Validate the effective Center endpoint without persisting it."""
        ...

    def validate_user_config_update(
        self,
        *,
        user_id: str,
        server_code: str,
        api_key: str | None,
        headers: dict[str, str] | None,
        endpoint_env: str | None,
        transport_protocol: str | None,
    ) -> dict[str, Any]: ...

    def update_user_unified_config(
        self,
        *,
        user_id: str,
        server_code: str,
        api_key: str | None,
        headers: dict[str, str] | None,
        endpoint_env: str | None,
        transport_protocol: str | None,
    ) -> dict[str, Any] | None: ...

    def rollback_unified_config(
        self,
        *,
        user_id: str,
        server_code: str,
        old_config: dict[str, Any] | None,
    ) -> None: ...

    def build_mcp_sync_payload(
        self,
        *,
        user_id: str,
        mcp_data: dict[str, Any],
        api_key: str | None = None,
        custom_headers: dict[str, str] | None = None,
        endpoint_env: str | None = None,
        transport_protocol: str | None = None,
        engine_type: str | None = None,
        bot_id: str | None = None,
        owner_id: str | None = None,
    ) -> tuple[str | None, dict[str, str], str, str | None]: ...
