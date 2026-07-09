"""Community device-sync — no-op dispatcher + plugin.

The community distribution does not ship a container runtime (the OSS device
runtime is owned by the BaaS team and is out of scope for the backend
de-vendoring — see the B6 spec). So community device sync is an honest no-op:
``CommunityDeviceSyncDispatcher(ctx)`` returns a
:class:`CommunityDeviceSyncPlugin` whose every method is a no-op.

This is a **real** community impl (not a ``MockSeam``) — it implements the core
``DeviceSyncDispatcher`` seam and imports only ``core`` / ``plugin_api``,
never ``plugins.prod`` / ``plugins.local``, so it satisfies the community column
isolation guard.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_sync import DeviceSyncPlugin

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context import DeviceContext

logger = get_logger()

_NOOP_MESSAGE = "community mode — device sync not configured (no container runtime)"


class CommunityDeviceSyncPlugin(DeviceSyncPlugin):
    """No-op :class:`DeviceSyncPlugin` for the community profile.

    Symlink / bot-config sync return a uniform ``{"success": False}`` result
    so callers degrade gracefully; the MCP bool methods return ``True`` so the
    multi-bot batch push counts the bot (Option B parity with the local/teclaw
    whole-artifact impls) without making any network call.
    """

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


class CommunityDeviceSyncDispatcher:
    """No-op ``DeviceSyncDispatcher`` for community.

    Returns a :class:`CommunityDeviceSyncPlugin` for any ``ctx`` — community
    bots have no remote device to push to.
    """

    def dispatch(self, ctx: "DeviceContext") -> DeviceSyncPlugin:
        logger.info(
            "[CommunityDeviceSyncDispatcher] no-op (bot=%s, provider=%s)",
            getattr(ctx, "bot_id", "?"),
            getattr(ctx, "provider", "?"),
        )
        return CommunityDeviceSyncPlugin()
