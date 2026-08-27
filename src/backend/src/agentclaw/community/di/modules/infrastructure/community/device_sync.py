"""Community DeviceSync dispatcher wiring — routing only.

This module composes; it does not construct. Each provider owns how its own
``DeviceSync`` is built, in its own module:

* ``baas`` → :class:`BaasDeviceSyncFactory` (``baas_device_sync.py``) —
  per-domain push over the invoke-http transport.
* ``teclaw`` → :class:`TeclawDeviceSyncFactory` (``teclaw_device_sync.py``) —
  whole-artifact delivery: re-composes the bot's full ``BotConfigArtifact`` and
  POSTs it to the running container.

Both are installed here and injected into :meth:`device_sync_dispatcher`, which
does nothing but pick one by ``ctx.provider``. Adding a provider means adding a
module and a routing entry — no edit to the other providers' construction, and
no shared bag of collaborators between them.

The routed set matches ``device_context_resolver._BINDING_ROUTED_PROVIDERS``.
"""
from __future__ import annotations

from typing import Callable

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import (
    TECLAW_DEVICE_PROVIDER,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.di.modules.infrastructure.community.baas_device_sync import (
    BaasDeviceSyncFactory,
    BaasDeviceSyncModule,
)
from agentclaw.community.di.modules.infrastructure.community.teclaw_device_sync import (
    TeclawDeviceSyncFactory,
    TeclawDeviceSyncModule,
)
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher


class CommunityDeviceSyncModule(Module):
    """Install the per-provider DeviceSync modules and bind the dispatcher."""

    def __init__(
        self,
        device_sync_wrapper: Callable[[DeviceSync], DeviceSync] | None = None,
    ) -> None:
        # Forwarded to the BaaS module: the wrapper decorates that provider's
        # service specifically (singlebox defers ``sync_all_mcp_servers``), so
        # it belongs with the factory it wraps, not at this routing layer.
        self._device_sync_wrapper = device_sync_wrapper

    def configure(self, binder: Binder) -> None:
        binder.install(BaasDeviceSyncModule(self._device_sync_wrapper))
        binder.install(TeclawDeviceSyncModule())

    @singleton
    @provider
    @inject
    def device_sync_dispatcher(
        self,
        baas_factory: BaasDeviceSyncFactory,
        teclaw_factory: TeclawDeviceSyncFactory,
    ) -> DeviceSyncDispatcher:
        from agentclaw.community.plugins.community.device_sync_dispatcher import (
            CommunityDeviceSyncDispatcher,
        )

        routes: dict[str, Callable[[DeviceContext], DeviceSync]] = {
            "baas": baas_factory,
            TECLAW_DEVICE_PROVIDER: teclaw_factory,
        }

        def device_sync(ctx: DeviceContext) -> DeviceSync:
            factory = routes.get(ctx.provider)
            if factory is None:
                # Unreachable while this map and the dispatcher's guard set
                # agree; raised rather than KeyError so a future drift between
                # them surfaces as the error dispatch callers already handle.
                raise UnknownProviderError(
                    f"CommunityDeviceSyncModule: no DeviceSync factory for "
                    f"provider={ctx.provider!r} (bot={ctx.bot_id})"
                )
            return factory(ctx)

        return CommunityDeviceSyncDispatcher(device_sync_factory=device_sync)
