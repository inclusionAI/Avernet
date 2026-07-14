"""TeClaw bot plugin Protocol — contract for TeClaw device lifecycle.

Defines the async Protocol that RealTeClawBotPlugin and StubTeClawBotPlugin
must implement. Methods expose domain-level TeClaw semantics; all HTTP
details are plugin-internal concerns (per D-02).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ._types import (
    _BotCreateResult,
    _BotDestroyResult,
    _BotInfo,
    _BotRestartResult,
    _BotUpdateResult,
)

if TYPE_CHECKING:
    from secbaas.community.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo


class TeClawBotPlugin(Protocol):
    """Async protocol for TeClaw bot lifecycle operations.

    Implementations:
    - RealTeClawBotPlugin: wraps the TeClaw HTTP API (aiohttp-based)
    - StubTeClawBotPlugin: in-memory mock for unit/integration tests

    All methods are async — TeClaw operations involve I/O over HTTP.
    """

    async def create_bot(self, bot_config: dict[str, Any]) -> _BotCreateResult:
        """Create a new bot on the TeClaw platform.

        Args:
            bot_config: Bot configuration dict (opaque passthrough from caller).

        Returns:
            _BotCreateResult with teclaw_bot_id, status, and optional config.
        """
        ...

    async def destroy_bot(self, bot_id: str) -> _BotDestroyResult:
        """Destroy (delete) a bot on the TeClaw platform.

        Args:
            bot_id: The teclaw_bot_id to destroy.

        Returns:
            _BotDestroyResult with teclaw_bot_id and status.
        """
        ...

    async def update_bot(
        self, bot_id: str, bot_config: dict[str, Any]
    ) -> _BotUpdateResult:
        """Update a bot's configuration on the TeClaw platform.

        Args:
            bot_id: The teclaw_bot_id to update.
            bot_config: New bot configuration dict (opaque passthrough).

        Returns:
            _BotUpdateResult with teclaw_bot_id, status, and optional config.
        """
        ...

    async def restart_bot(self, bot_id: str) -> _BotRestartResult:
        """Restart a bot by re-applying its last-known configuration.

        Internally proxies to the UPDATE operation with the cached config.

        Args:
            bot_id: The teclaw_bot_id to restart.

        Returns:
            _BotRestartResult with teclaw_bot_id and status.
        """
        ...

    async def get_bot(self, bot_id: str) -> _BotInfo:
        """Get a bot's current info from the TeClaw platform.

        Args:
            bot_id: The teclaw_bot_id to query.

        Returns:
            _BotInfo with teclaw_bot_id, status, and optional config.
        """
        ...

    async def resolve_http_conn_info(
        self, bot_id: str, port: int, path: str, template_id: int | None = None
    ) -> HttpConnectionInfo:
        """Resolve HTTP connection info for invoking endpoints on a bot.

        Constructs a local HTTP URL targeting the TeClaw-managed device.

        Args:
            bot_id: The teclaw_bot_id for the target device.
            port: Target port on the device.
            path: HTTP path (e.g., "/api/openclaw/invoke").
            template_id: Optional template ID (int) for multi-tenant target format.

        Returns:
            HttpConnectionInfo with http_url, token, and target.
        """
        ...

    async def resolve_ws_conn_info(
        self, bot_id: str, port: int, path: str, template_id: int | None = None
    ) -> WsConnectionInfo:
        """Resolve WebSocket connection info for a bot device.

        Constructs a local WebSocket URL targeting the TeClaw-managed device.

        Args:
            bot_id: The teclaw_bot_id for the target device.
            port: Target port on the device.
            path: WebSocket path (e.g., "/api/openclaw/ws").
            template_id: Optional template ID (int) for multi-tenant target format.

        Returns:
            WsConnectionInfo with ws_url, token, target, and expires_at.
        """
        ...

    async def update_outbound_rule(self, bot_id: str, rules: dict[str, Any]) -> bool:
        """Update the outbound operation rule for a bot via PUT /api/v1/bot/{bot_id}/operationRules.

        Args:
            bot_id: The teclaw_bot_id for the target device.
            rules: Dict in TeClaw API JSON format, e.g.
                ``{"header_operation_rules": [...]}``.

        Returns:
            True if the update was accepted by the TeClaw API.
        """
        ...

    async def close(self) -> None:
        """Release resources held by the plugin (e.g., aiohttp session)."""
        ...
