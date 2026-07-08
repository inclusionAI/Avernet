"""DeviceSyncDispatcher — the per-bot device-sync routing seam.

A pure ``DeviceContext -> DeviceSyncPlugin`` factory: given a resolved
:class:`DeviceContext`, return the per-bot :class:`DeviceSyncPlugin`. Unlike
``DeviceFilesystemDispatcher`` it carries **no** provider-agnostic logic (no path
mappers, no validation), so there is no core holder class — the seam IS this
``Protocol`` and consumers inject it directly. The vendor plugin construction
lives entirely in the per-profile impl:

- corp / test  → ``plugins.prod.device_sync.ProdDeviceSyncDispatcher``
  (baas / arca / teclaw construction)
- community    → ``plugins.community.device_sync.CommunityDeviceSyncDispatcher``
  (no-op — no OSS container runtime, BaaS-team-owned, out of B6 scope)

Core owns the abstraction (DIP); the impls depend on core, never the other way
round — so ``core/`` carries no ``arca`` / ``plugins.prod`` symbol. Each profile
binds its impl to this key; callers reach it via
``resolver.resolve_for_bot(...) -> ctx`` then ``dispatcher.dispatch(ctx)``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context import DeviceContext
    from agentclaw.community.plugin_api.device_sync import DeviceSyncPlugin


@runtime_checkable
class DeviceSyncDispatcher(Protocol):
    """Per-bot ``DeviceContext -> DeviceSyncPlugin`` dispatcher (the DI seam)."""

    def dispatch(self, ctx: "DeviceContext") -> "DeviceSyncPlugin":
        """Return the :class:`DeviceSyncPlugin` for ``ctx`` (by ``ctx.provider``)."""
        ...
