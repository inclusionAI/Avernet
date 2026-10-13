"""Metrics concern — test / singlebox binding (NoopMetrics)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.metrics import MetricsPlugin


class TestMetricsModule(Module):
    """test / singlebox: NoopMetrics (no metric capture)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.metrics import NoopMetrics

        binder.bind(MetricsPlugin, to=NoopMetrics, scope=singleton)