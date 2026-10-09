"""Metrics concern — community binding (CommunityMetrics)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.metrics import MetricsPlugin


class CommunityMetricsModule(Module):
    """community: CommunityMetrics (in-proc summaries, no exporter)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.metrics import CommunityMetrics

        binder.bind(MetricsPlugin, to=CommunityMetrics, scope=singleton)