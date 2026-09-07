"""TeClaw bot plugin Protocol — contract for TeClaw device lifecycle.

Defines the async Protocol that RealTeClawBotPlugin and StubTeClawBotPlugin
must implement. Methods expose domain-level TeClaw semantics; all HTTP
details are plugin-internal concerns (per D-02).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from secbaas.community.api.device_manage import DeviceCallbackContext
from ._types import (
    BotAsyncTaskResult,
    BotCreateResult,
    BotDestroyResult,
    BotInfo,
    BotRestartResult,
    BotUpdateResult,
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

    async def create_bot(
        self,
        bot_config: dict[str, Any],
        *,
        callback_context: DeviceCallbackContext | None = None,
    ) -> BotCreateResult | BotAsyncTaskResult:
        """Create a new bot on the TeClaw platform.

        When ``callback_context`` is provided, the TeClaw platform POSTs a
        ``TeclawCallbackRequest`` to ``callback_context.callback_url`` on
        operation completion and the plugin returns ``BotAsyncTaskResult``
        carrying ``task_id`` for correlation.

        Args:
            bot_config: Bot configuration dict (opaque passthrough from caller).
            callback_context: BaaS-side identifiers for callback routing.

        Returns:
            ``BotAsyncTaskResult`` with ``task_id`` when ``callback_context``
            is provided, otherwise ``BotCreateResult`` with teclaw_bot_id.
        """
        ...

    async def destroy_bot(self, bot_id: str) -> BotDestroyResult:
        """Destroy (delete) a bot on the TeClaw platform.

        Args:
            bot_id: The teclaw_bot_id to destroy.

        Returns:
            BotDestroyResult with teclaw_bot_id and status.
        """
        ...

    async def update_bot(
        self,
        bot_id: str,
        bot_config: dict[str, Any],
        *,
        callback_context: DeviceCallbackContext | None = None,
    ) -> BotUpdateResult | BotAsyncTaskResult:
        """Update a bot's configuration on the TeClaw platform.

        When ``callback_context`` is provided, the TeClaw platform POSTs a
        ``TeclawCallbackRequest`` to ``callback_context.callback_url`` on
        operation completion and the plugin returns ``BotAsyncTaskResult``
        carrying ``task_id`` for correlation.

        Args:
            bot_id: The teclaw_bot_id to update.
            bot_config: New bot configuration dict (opaque passthrough).
            callback_context: BaaS-side identifiers for callback routing.

        Returns:
            ``BotAsyncTaskResult`` with ``task_id`` when ``callback_context``
            is provided, otherwise ``BotUpdateResult`` with teclaw_bot_id.
        """
        ...

    async def restart_bot(
        self,
        bot_id: str,
        *,
        callback_context: DeviceCallbackContext | None = None,
    ) -> BotRestartResult | BotAsyncTaskResult:
        """Restart a bot by re-applying its last-known configuration.

        Internally proxies to the UPDATE operation with the cached config.

        When ``callback_context`` is provided, the TeClaw platform POSTs a
        ``TeclawCallbackRequest`` to ``callback_context.callback_url`` on
        operation completion and the plugin returns ``BotAsyncTaskResult``
        carrying ``task_id`` for correlation.

        Args:
            bot_id: The teclaw_bot_id to restart.
            callback_context: BaaS-side identifiers for callback routing.

        Returns:
            ``BotAsyncTaskResult`` with ``task_id`` when ``callback_context``
            is provided, otherwise ``BotRestartResult`` with teclaw_bot_id.
        """
        ...

    async def get_bot(self, bot_id: str) -> BotInfo:
        """Get a bot's current info from the TeClaw platform.

        Args:
            bot_id: The teclaw_bot_id to query.

        Returns:
            BotInfo with teclaw_bot_id, status, and optional config.
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
