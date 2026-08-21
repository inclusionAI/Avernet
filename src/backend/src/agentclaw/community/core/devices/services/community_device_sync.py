"""CommunityDeviceSyncService — Core no-op DeviceSync for the community profile.

The community distribution ships no container runtime, so community device
sync is an honest
no-op: every symlink/bot-config call returns a uniform ``{"success": False}``
result so callers degrade gracefully, and the MCP bool methods return ``True``
so the multi-bot batch push counts the bot (Option B parity with the local/
teclaw whole-artifact impls) without making any network call.

This is a Core service rather than a Plugin implementation. It is constructed
by the
``CommunityDeviceSyncModule`` DI root and returned by
``CommunityDeviceSyncDispatcher.dispatch(ctx)`` for any ``ctx``.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.log import get_logger

logger = get_logger()

_NOOP_MESSAGE = "community mode — device sync not configured (no container runtime)"


class CommunityDeviceSyncService(DeviceSync):
    """No-op Core :class:`DeviceSync` for the community profile."""

    def sync_symlinks(self, symlinks: list[dict[str, str]]) -> dict[str, Any]:
        return {"success": False, "message": _NOOP_MESSAGE}

    def sync_bot_config(
        self,
        bot_id: str,
        binding_id: int,
        public: str,
        permission_owner: Optional[str],
        user_id: str,
        nick_name: str,
    ) -> dict[str, Any]:
        return {"success": False, "message": _NOOP_MESSAGE}

    def sync_all_mcp_servers(self, mcp_servers: list[dict[str, Any]]) -> bool:
        return True

    def sync_single_mcp(
        self,
        mcp_data: dict[str, Any],
        *,
        api_key: Optional[str] = None,
        custom_headers: Optional[dict[str, str]] = None,
        endpoint_env: str = "PROD",
        transport_protocol: Optional[str] = None,
    ) -> bool:
        return True

    def sync_remove_mcp(self, server_code: str) -> bool:
        return True

    def has_mcp(self, server_code: str) -> bool:
        return True