"""Health concern — community binding (direct-HTTP engine health probe)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.health_probe import HealthProbePlugin


class CommunityHealthModule(Module):
    """community: ``CommunityHealthProbe`` (direct-HTTP /readiness, no proxypass)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.health_probe import CommunityHealthProbe

        binder.bind(HealthProbePlugin, to=CommunityHealthProbe, scope=singleton)
