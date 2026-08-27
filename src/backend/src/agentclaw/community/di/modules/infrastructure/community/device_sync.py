"""Community DeviceSync dispatcher wiring.

Community devices are BaaS-backed. The module binds only the Plugin dispatcher
and injects a factory for the shared Core ``BaasDeviceSyncService``.
"""
from __future__ import annotations

from typing import Callable

from injector import Module, inject, provider, singleton

from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService
from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher


class CommunityDeviceSyncModule(Module):
    """Bind the shared BaaS dispatcher, optionally adapting its Core service."""

    def __init__(
        self,
        device_sync_wrapper: Callable[[DeviceSync], DeviceSync] | None = None,
    ) -> None:
        self._device_sync_wrapper = device_sync_wrapper

    @singleton
    @provider
    @inject
    def device_sync_dispatcher(
        self,
        baas_service: BaasService,
    ) -> DeviceSyncDispatcher:
        from agentclaw.community.plugins.community.device_sync_dispatcher import (
            CommunityDeviceSyncDispatcher,
        )

        def baas_device_sync(ctx: DeviceContext) -> DeviceSync:
            conn_info = ctx.conn_info
            transport = BaasInvokeTransport(
                bind_id=conn_info["bind_id"],
                engine_port=conn_info["engine_port"],
                tenant=conn_info.get("tenant", ""),
                baas_service=baas_service,
                device_uuid=conn_info.get("device_uuid"),
            )
            service: DeviceSync = BaasDeviceSyncService(
                transport=transport,
                conn_info=conn_info,
            )
            if self._device_sync_wrapper is not None:
                service = self._device_sync_wrapper(service)
            return service

        return CommunityDeviceSyncDispatcher(device_sync_factory=baas_device_sync)
