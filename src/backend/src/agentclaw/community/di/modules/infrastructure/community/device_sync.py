"""Community device-runtime no-ops (BaaS-team-owned runtime is out of B6 scope).

Community ships no container runtime, so the device-runtime seams that DON'T
gate the community injector are bound to honest no-ops here:

- ``DeviceSyncDispatcher`` → :class:`CommunityDeviceSyncDispatcher`
- ``DeviceAdapterTransport`` → :class:`CommunityDeviceAdapterTransport` (keeps the
  base-list ``CronRelayService`` constructable in community; it previously rode
  ``cron_module``'s prod binding, removed in B6 T26).

The heavier device-runtime keys (``DeviceAccessor`` / ``DeviceFileSystemResolver``)
stay unbound — community has no filesystem/device-plugin runtime. Imports only
``plugins.community`` / ``core`` / ``plugin_api`` — never ``plugins.prod`` /
``plugins.local`` — per the community isolation guard.
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.core.devices.services.device_sync_dispatcher import (
    DeviceSyncDispatcher,
)
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport


class CommunityDeviceSyncModule(Module):
    """community: no-op device-sync dispatcher + adapter transport."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.device_sync import (
            CommunityDeviceSyncDispatcher,
        )
        from agentclaw.community.plugins.community.device_adapter_transport import (
            CommunityDeviceAdapterTransport,
        )

        binder.bind(
            DeviceSyncDispatcher,
            to=CommunityDeviceSyncDispatcher,
            scope=singleton,
        )
        binder.bind(
            DeviceAdapterTransport,
            to=CommunityDeviceAdapterTransport,
            scope=singleton,
        )
