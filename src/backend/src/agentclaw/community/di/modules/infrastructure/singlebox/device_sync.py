"""Singlebox DeviceSync dispatcher wiring over the shared BaaS service."""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.core.devices.services.baas_device_sync import BaasDeviceSyncService
from agentclaw.community.core.devices.services.baas_invoke_transport import BaasInvokeTransport
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher


class SingleboxDeviceSyncModule(Module):
    """Bind the local-profile dispatcher to the shared BaaS DeviceSync service."""

    @singleton
    @provider
    @inject
    def device_sync_dispatcher(self, baas_service: BaasService) -> DeviceSyncDispatcher:
        from agentclaw.community.plugins.local.device_sync_dispatcher import (
            LocalDeviceSyncDispatcher,
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
            return BaasDeviceSyncService(transport=transport, conn_info=conn_info)

        return LocalDeviceSyncDispatcher(device_sync_factory=baas_device_sync)
