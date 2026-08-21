"""Community device-runtime no-ops (BaaS-team-owned runtime is out of B6 scope).

Community ships no container runtime, so the device-runtime seams that DON'T
gate the community injector are bound to honest no-ops here:

- ``DeviceSyncDispatcher`` → :class:`CommunityDeviceSyncDispatcher` (selection
  only; receives a DI-constructed Core ``DeviceSync`` factory)
- ``DeviceAdapterTransport`` → :class:`CommunityDeviceAdapterTransport` (keeps the
  base-list ``CronRelayService`` constructable in community; it previously rode
  ``cron_module``'s prod binding, removed in B6 T26).

The heavier device-runtime keys (``DeviceAccessor`` / ``DeviceFileSystemResolver``)
stay unbound — community has no filesystem/device-plugin runtime. Imports only
``plugins.community`` / ``core`` / ``plugin_api`` — never ``plugins.prod`` /
``plugins.local`` — per the community isolation guard.
"""
from __future__ import annotations

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher


class CommunityDeviceSyncModule(Module):
    """community: no-op device-sync dispatcher + adapter transport."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.core.devices.services.community_device_sync import (
            CommunityDeviceSyncService,
        )
        from agentclaw.community.plugins.community.device_adapter_transport import (
            CommunityDeviceAdapterTransport,
        )
        # Bind the Core service here; the Plugin receives only a DeviceSync
        # factory and does not import this concrete service.
        binder.bind(
            CommunityDeviceSyncService,
            to=CommunityDeviceSyncService,
            scope=singleton,
        )
        binder.bind(
            DeviceAdapterTransport,
            to=CommunityDeviceAdapterTransport,
            scope=singleton,
        )

    @singleton
    @provider
    @inject
    def device_sync_dispatcher(
        self,
        injector: Injector,
    ) -> DeviceSyncDispatcher:
        from agentclaw.community.core.devices.services.community_device_sync import (
            CommunityDeviceSyncService,
        )
        from agentclaw.community.plugins.community.device_sync_dispatcher import (
            CommunityDeviceSyncDispatcher,
        )

        return CommunityDeviceSyncDispatcher(
            device_sync_factory=lambda: injector.get(CommunityDeviceSyncService),
        )
