"""LocalDeviceSyncDispatcher — Rule 20 LOCAL dispatcher Plugin (selection only).

New Rule 20 entry (CHG-6). A selection-only dispatcher that nominally inherits
:class:`DeviceSyncDispatcher` and is decorated ``@plugin_impl(mode=LOCAL)``.
It selects a DI-injected :class:`LocalDeviceSyncService` (Core). It contains
selection only — no HTTP, filesystem, or provider workflow — and is NOT bound
as the general singlebox/test/community dispatcher (``CommunityDeviceSyncModule``
binds ``CommunityDeviceSyncDispatcher`` for those profiles). Exposing complete
Singlebox DeviceSync is out of scope for this refactor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from injector import inject

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_sync import DeviceSync
from agentclaw.community.core.devices.services.local_device_sync import (
    LocalDeviceSyncService,
)
from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="local DeviceSync dispatcher (selection only, not general-bound)",
)
class LocalDeviceSyncDispatcher(DeviceSyncDispatcher):
    """LOCAL dispatcher: returns the DI-injected Local service for any ``ctx``.

    Satisfies Rule 20 without activating complete Singlebox DeviceSync.
    """

    @inject
    def __init__(
        self,
        local_device_sync_service: LocalDeviceSyncService,
    ) -> None:
        self._service = local_device_sync_service

    def dispatch(self, ctx: "DeviceContext") -> DeviceSync:
        return self._service