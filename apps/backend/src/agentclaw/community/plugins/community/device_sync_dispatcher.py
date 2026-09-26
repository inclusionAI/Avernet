"""CommunityDeviceSyncDispatcher — Rule 20 community dispatcher Plugin.

A selection-only dispatcher that inherits :class:`DeviceSyncDispatcher` and is
decorated ``@plugin_impl``. It holds a DI-injected
``Callable[[DeviceContext], DeviceSync]`` factory and returns its Core service for any
``ctx``, Concrete service construction stays in
the DI module.

Both binding-routed providers are accepted: ``baas`` (per-domain push through
the invoke-http transport) and ``teclaw`` (whole-artifact delivery). They share
the BaaS runtime transport but differ in delivery strategy, so the *factory*
picks the concrete service off ``ctx.provider`` — this dispatcher stays
selection-only and just guards the provider set.
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

# Providers this dispatcher routes. Mirrors
# ``device_context_resolver._BINDING_ROUTED_PROVIDERS`` — every binding-routed
# provider resolves to a community DeviceSync service.
_SUPPORTED_PROVIDERS = frozenset({"baas", "teclaw"})


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="singlebox reuses the community BaaS DeviceSync dispatcher",
)
class CommunityDeviceSyncDispatcher(DeviceSyncDispatcher):
    """``DeviceSyncDispatcher`` for community (baas + teclaw).

    Returns the DI-factory-produced ``DeviceSync`` for ``ctx``.
    """

    @inject
    def __init__(self, device_sync_factory: Callable[[DeviceContext], DeviceSync]) -> None:
        self._device_sync_factory = device_sync_factory

    def dispatch(self, ctx: DeviceContext) -> DeviceSync:
        from agentclaw.community.core.devices.services.device_context import (
            UnknownProviderError,
        )

        if ctx.provider not in _SUPPORTED_PROVIDERS:
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
