"""Singlebox DeviceSync dispatcher.

The Plugin selects a DI-provided Core ``DeviceSync`` service for the resolved
device context. Singlebox wiring supplies the shared BaaS implementation.
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
    rationale="singlebox BaaS DeviceSync dispatcher (selection only)",
)
class LocalDeviceSyncDispatcher(DeviceSyncDispatcher):
    """LOCAL dispatcher: returns the DI-factory-produced service for any ``ctx``.

    Satisfies Rule 20 while reusing the shared BaaS DeviceSync service.
    """

    @inject
    def __init__(
        self,
        device_sync_factory: Callable[[DeviceContext], DeviceSync],
    ) -> None:
        self._device_sync_factory = device_sync_factory

    def dispatch(self, ctx: DeviceContext) -> DeviceSync:
        return self._device_sync_factory(ctx)
