"""LocalDeviceSyncDispatcher — Rule 20 LOCAL dispatcher Plugin (selection only).

A selection-only dispatcher that inherits :class:`DeviceSyncDispatcher` and
is decorated ``@plugin_impl(mode=LOCAL)``.
It selects a DI-injected ``Callable[[], DeviceSync]`` factory. It contains
selection only — no HTTP, filesystem, or provider workflow — and is NOT bound
as the general singlebox/test/community dispatcher (``CommunityDeviceSyncModule``
binds ``CommunityDeviceSyncDispatcher`` for those profiles). Exposing complete
Singlebox DeviceSync is out of scope for this refactor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from injector import inject

from agentclaw.community.core.devices.services.device_sync import DeviceSync

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context import DeviceContext

from agentclaw.community.plugin_api.device_sync_dispatcher import DeviceSyncDispatcher
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="local DeviceSync dispatcher (selection only, not general-bound)",
)
class LocalDeviceSyncDispatcher(DeviceSyncDispatcher):
    """LOCAL dispatcher: returns the DI-factory-produced service for any ``ctx``.

    Satisfies Rule 20 without activating complete Singlebox DeviceSync.
    """

    @inject
    def __init__(
        self,
        device_sync_factory: Callable[[], DeviceSync],
    ) -> None:
        self._device_sync_factory = device_sync_factory

    def dispatch(self, ctx: "DeviceContext") -> DeviceSync:
        return self._device_sync_factory()
