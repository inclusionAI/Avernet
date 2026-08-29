"""DeviceSync -- Core Protocol owning the six-method device-sync contract.

This is the non-Plugin Core contract for pushing symlink/bot-config/MCP
changes to a device runtime. It deliberately does NOT inherit ``Plugin`` nor
use ``@plugin_impl`` -- it is a plain ``Protocol`` consumed by Core callers and
returned by the Plugin Protocol :class:`DeviceSyncDispatcher` (see
``plugin_api/device_sync_dispatcher.py``). Concrete behavior lives in shared Core services (including the BaaS service)
and is selected by the dispatcher implementations. Provider-specific Corp
services remain in the Corp package.

``DeviceSyncUnavailableError`` is part of the Core contract surface so
callers do not depend on a concrete dispatcher implementation.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


class DeviceSyncUnavailableError(RuntimeError):
    """Raised by the device-sync dispatcher when the requested bot has
    no syncable device (no binding, wrong provider, missing sandbox in
    prod, …). Callers typically catch this and return a
    ``{"success": False, "message": ...}`` result.
    """


@runtime_checkable
class DeviceSync(Protocol):
    """Push symlink configuration or other payloads to a device.

    The six methods are synchronous (callers wrap them in
    ``asyncio.to_thread``). Result dicts carry at least
    ``{"success": bool, "message": str}``; the MCP methods return ``bool``.
    """

    def sync_symlinks(
        self,
        symlinks: list[dict[str, str]],
        *,
        effective_mcps: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Sync symlink mappings to the device.

        Args:
            symlinks: List of ``{"source": ..., "target": ...}`` mappings.
            effective_mcps: The bot's already-resolved effective MCP set, when
                the caller has one. Only a whole-artifact device reads it —
                such a device ignores ``symlinks`` and recomposes the bot's
                whole configuration from the database, and this spares that
                compose a re-read of a set the caller just resolved. An
                implementation that consumes ``symlinks`` directly composes
                nothing and ignores it. Callers pass it only when they have
                it, so an implementation is never *required* to accept it.

        Returns:
            Result dict with at least ``{"success": bool, "message": str}``.
        """
        ...

    def sync_bot_config(
        self,
        bot_id: str,
        binding_id: int,
        public: str,
        permission_owner: Optional[str],
        user_id: str,
        nick_name: str,
    ) -> dict[str, Any]:
        """Sync bot ROLE/VISIBILITY configuration to the device.

        Args:
            bot_id: Bot ID (logged for traceability).
            binding_id: Device binding ID. ``0``/``None`` means the bot
                has no device binding; impl returns a "no binding" result
                without making the HTTP call.
            public: ``"1"`` (PUBLIC) or ``"0"`` (PRIVATE).
            permission_owner: ``"OWNER"`` / ``"CALLER"`` (or ``None`` to omit).
            user_id: User ID (for prod connection lookups; ignored locally).
            nick_name: User nick name (for prod connection lookups).

        Returns:
            Result dict with at least ``{"success": bool, "message": str}``.
        """
        ...

    # ── MCP delivery ─────────────────────────────────────────────────────
    # Folded in from the former ``DeviceMCPSyncPlugin`` so MCP delivery rides
    # the same per-bot, provider-routed boundary as symlink/bot-config sync.
    # These are **synchronous** (like the methods above; callers wrap them in
    # ``asyncio.to_thread``). The device connection is captured at construction
    # — no per-call ``conn_info`` param. Per impl: arca/baas push per-MCP over
    # ``/api/mcp``; teclaw delivers the whole composed artifact; local is no-op.

    def sync_all_mcp_servers(self, mcp_servers: list[dict[str, Any]]) -> bool:
        """Declare the full set of allowed MCP servers to the device
        (filter-servers). Returns ``True`` on success."""
        ...

    def sync_single_mcp(
        self,
        mcp_data: dict[str, Any],
        *,
        api_key: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        endpoint_env: str = "PROD",
        transport_protocol: Optional[str] = None,
    ) -> bool:
        """Push a single MCP server config to the device. Returns ``True`` on
        success. May raise on transport error (parity with the legacy impl)."""
        ...

    def sync_remove_mcp(self, server_code: str) -> bool:
        """Remove a single MCP server config from the device. Returns ``True``
        on success."""
        ...

    def has_mcp(self, server_code: str) -> bool:
        """Probe whether the device reports the MCP installed.

        Used by the multi-bot batch push to skip devices that don't have the
        MCP. Whole-artifact devices such as Teclaw return ``True`` so the
        batch always delivers and counts them.
        """
        ...