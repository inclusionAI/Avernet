"""BaaS ``DeviceSync`` construction — the ``provider=baas`` DI component.

Owns one thing: how a resolved :class:`DeviceContext` becomes a
:class:`BaasDeviceSyncService` over the invoke-http transport. It knows nothing
about routing, about which other providers exist, or about the dispatcher that
will consume it — ``CommunityDeviceSyncModule`` installs this module and injects
the factory it binds.

The singlebox ``device_sync_wrapper`` lives here rather than at the routing
layer because it decorates *this* provider's service: it defers
``sync_all_mcp_servers`` because the OpenClaw engine serves
``/api/mcp/filter-servers`` through the mcporter CLI, which the singlebox
runtime lacks. That is a per-domain BaaS concern, so a provider that does not
issue that call must not inherit the wrapper.
"""
from __future__ import annotations

from typing import Callable

from injector import Module, inject, provider, singleton

from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService
from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.service_bot.services.baas_service import BaasService


class BaasDeviceSyncFactory:
    """Builds the per-bot BaaS ``DeviceSync`` for a resolved ``DeviceContext``.

    A callable rather than a bare function so it is an injectable type with a
    name the dispatcher module can depend on.
    """

    def __init__(
        self,
        *,
        baas_service: BaasService,
        device_sync_wrapper: Callable[[DeviceSync], DeviceSync] | None = None,
    ) -> None:
        self._baas_service = baas_service
        self._device_sync_wrapper = device_sync_wrapper

    def __call__(self, ctx: DeviceContext) -> DeviceSync:
        conn_info = ctx.conn_info
        transport = BaasInvokeTransport(
            bind_id=conn_info["bind_id"],
            engine_port=conn_info["engine_port"],
            tenant=conn_info.get("tenant", ""),
            baas_service=self._baas_service,
            device_uuid=conn_info.get("device_uuid"),
        )
        service: DeviceSync = BaasDeviceSyncService(
            transport=transport,
            conn_info=conn_info,
        )
        if self._device_sync_wrapper is not None:
            service = self._device_sync_wrapper(service)
        return service


class BaasDeviceSyncModule(Module):
    """Bind :class:`BaasDeviceSyncFactory`, optionally wrapping its service."""

    def __init__(
        self,
        device_sync_wrapper: Callable[[DeviceSync], DeviceSync] | None = None,
    ) -> None:
        self._device_sync_wrapper = device_sync_wrapper

    @singleton
    @provider
    @inject
    def baas_device_sync_factory(
        self, baas_service: BaasService
    ) -> BaasDeviceSyncFactory:
        return BaasDeviceSyncFactory(
            baas_service=baas_service,
            device_sync_wrapper=self._device_sync_wrapper,
        )
