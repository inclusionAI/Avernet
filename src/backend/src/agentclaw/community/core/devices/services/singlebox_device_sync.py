"""Singlebox DeviceSync compatibility wrapper.

Singlebox uses the shared BaaS transport for device operations.  The one
exception is full MCP whitelist synchronization: the OpenClaw Engine currently
implements ``/api/mcp/filter-servers`` through the external ``mcporter`` CLI,
which is not available in the current Singlebox runtime.  That operation is
therefore deferred here while the remaining DeviceSync methods delegate to the
shared BaaS service.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.log import get_logger

logger = get_logger()


class SingleboxDeviceSyncService(DeviceSync):
    """Singlebox DeviceSync facade over a BaaS-backed implementation."""

    def __init__(self, delegate: DeviceSync) -> None:
        self._delegate = delegate

    def sync_symlinks(
        self,
        symlinks: list[dict[str, Any]],
        *,
        effective_mcps: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        # ``effective_mcps`` is a whole-artifact hint; the BaaS transport
        # behind this wrapper consumes the symlinks directly, so it is
        # accepted (the contract declares it) and dropped here.
        return self._delegate.sync_symlinks(symlinks)

    def sync_bot_config(
        self,
        bot_id: str,
        binding_id: int,
        public: str,
        permission_owner: Optional[str],
        user_id: str,
        nick_name: str,
    ) -> dict[str, Any]:
        return self._delegate.sync_bot_config(
            bot_id,
            binding_id,
            public,
            permission_owner,
            user_id,
            nick_name,
        )

    def sync_all_mcp_servers(self, mcp_servers: list[dict[str, Any]]) -> bool:
        """Defer whitelist sync until the Singlebox Engine has ``mcporter``."""
        logger.warning(
            "[SingleboxDeviceSyncService] skip MCP whitelist sync: "
            "the Singlebox Engine requires mcporter, which is unavailable "
            "in the current runtime"
        )
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
        return self._delegate.sync_single_mcp(
            mcp_data,
            api_key=api_key,
            custom_headers=custom_headers,
            endpoint_env=endpoint_env,
            transport_protocol=transport_protocol,
        )

    def sync_remove_mcp(self, server_code: str) -> bool:
        return self._delegate.sync_remove_mcp(server_code)

    def has_mcp(self, server_code: str) -> bool:
        return self._delegate.has_mcp(server_code)
