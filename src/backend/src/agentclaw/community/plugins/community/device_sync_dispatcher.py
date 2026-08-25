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
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context import DeviceContext

logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="singlebox reuses the community BaaS DeviceSync dispatcher",
)
class CommunityDeviceSyncDispatcher(DeviceSyncDispatcher):
    """BaaS ``DeviceSyncDispatcher`` for community.

    Returns the DI-factory-produced BaaS ``DeviceSync`` for ``ctx``.
    """

    @inject
    def __init__(self, device_sync_factory: Callable[[DeviceContext], DeviceSync]) -> None:
        self._device_sync_factory = device_sync_factory

    def dispatch(self, ctx: DeviceContext) -> DeviceSync:
        from agentclaw.community.core.devices.services.device_context import (
            UnknownProviderError,
        )

        if ctx.provider != "baas":
            raise UnknownProviderError(
                f"CommunityDeviceSyncDispatcher: unsupported provider={ctx.provider!r} "
                f"(bot={ctx.bot_id})"
            )

        logger.info(
            "[CommunityDeviceSyncDispatcher] route (bot=%s, provider=%s)",
            getattr(ctx, "bot_id", "?"),
            ctx.provider,
        )
        return self._device_sync_factory(ctx)
