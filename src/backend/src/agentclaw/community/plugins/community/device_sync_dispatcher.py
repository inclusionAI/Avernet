"""CommunityDeviceSyncDispatcher — Rule 20 community dispatcher Plugin.

A selection-only dispatcher that inherits :class:`DeviceSyncDispatcher` and is
decorated ``@plugin_impl``. It holds a DI-injected
``Callable[[DeviceContext], DeviceSync]`` factory and returns its Core service for any
``ctx``, Concrete service construction stays in
the DI module.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from injector import inject

from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context import DeviceContext

logger = get_logger()


@plugin_impl(
    mode=Mode.PROD,
    rationale="community BaaS DeviceSync dispatcher",
)
class CommunityDeviceSyncDispatcher(DeviceSyncDispatcher):
    """BaaS ``DeviceSyncDispatcher`` for community.

    Returns the DI-factory-produced BaaS ``DeviceSync`` for ``ctx``.
    """

    @inject
    def __init__(self, device_sync_factory: Callable[[DeviceContext], DeviceSync]) -> None:
        self._device_sync_factory = device_sync_factory

    def dispatch(self, ctx: DeviceContext) -> DeviceSync:
        logger.info(
            "[CommunityDeviceSyncDispatcher] route (bot=%s, provider=%s)",
            getattr(ctx, "bot_id", "?"),
            getattr(ctx, "provider", "?"),
        )
        return self._device_sync_factory(ctx)
