"""DeviceSyncDispatcher — the only DeviceSync-related Plugin Protocol.

A pure ``DeviceContext -> DeviceSync`` factory seam: given a resolved
:class:`DeviceContext`, return the per-bot Core :class:`DeviceSync` service.
This is the real selection seam (the Plugin Protocol) under ``plugin_api/``;
concrete Core behavior lives in Core services (Community/Local/Arca/BaaS/Teclaw)
and is selected by the dispatcher implementations (Local/Community/Prod) under
``plugins/``. Dispatchers contain selection only — they hold DI-injected
services or ``Callable[[DeviceContext], DeviceSync]`` factories and import no
concrete Core service.

Unlike ``DeviceFilesystemDispatcher`` this seam carries **no** provider-agnostic
logic (no path mappers, no validation), so there is no core holder class — the
seam IS this ``Plugin`` Protocol and consumers inject it directly. Each profile
binds its impl to this key; callers reach it via
``resolver.resolve_for_bot(...) -> ctx`` then ``dispatcher.dispatch(ctx)``.

The old Core-located ``core/devices/services/device_sync_dispatcher.py`` module
is deleted by CHG-17 (Task 14) after all production callers migrate.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context import DeviceContext
    from agentclaw.community.core.devices.services.device_sync import DeviceSync


@runtime_checkable
class DeviceSyncDispatcher(Plugin, Protocol):
    """Per-bot ``DeviceContext -> DeviceSync`` dispatcher (the DI seam).

    Implementations nominally inherit this Protocol and are decorated
    ``@plugin_impl`` so the Rule 20/21 registry recognizes the Plugin
    Protocol from the direct base class. The Core :class:`DeviceSync`
    deliberately does NOT inherit ``Plugin``.
    """

    def dispatch(self, ctx: "DeviceContext") -> "DeviceSync":
        """Return the Core :class:`DeviceSync` service for ``ctx``
        (selected by ``ctx.provider`` / ``ctx.bot_type``)."""
        ...