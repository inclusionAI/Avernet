"""CommunityDeviceSyncDispatcher — Rule 20 community dispatcher Plugin.

Moved out of ``plugins/community/device_sync.py`` (CHG-5). A selection-only
dispatcher that nominally inherits :class:`DeviceSyncDispatcher` and is
decorated ``@plugin_impl``. It holds a DI-injected
:class:`CommunityDeviceSyncService` (Core) and returns it for any ``ctx``,
preserving the no-op log line. It depends on Core ``DeviceSync`` /
``DeviceSyncDispatcher`` only and imports no concrete Core service outside DI.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from injector import inject

from agentclaw.community.core.devices.services.community_device_sync import (
    CommunityDeviceSyncService,
)
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context import DeviceContext

logger = get_logger()


@plugin_impl(
    mode=Mode.PROD,
    rationale="community no-op DeviceSync dispatcher",
)
class CommunityDeviceSyncDispatcher(DeviceSyncDispatcher):
    """No-op ``DeviceSyncDispatcher`` for community.

    Returns the DI-injected :class:`CommunityDeviceSyncService` for any
    ``ctx`` — community bots have no remote device to push to.
    """

    @inject
    def __init__(self, community_device_sync_service: CommunityDeviceSyncService) -> None:
        self._service = community_device_sync_service

    def dispatch(self, ctx: "DeviceContext") -> DeviceSync:
        logger.info(
            "[CommunityDeviceSyncDispatcher] no-op (bot=%s, provider=%s)",
            getattr(ctx, "bot_id", "?"),
            getattr(ctx, "provider", "?"),
        )
        return self._service