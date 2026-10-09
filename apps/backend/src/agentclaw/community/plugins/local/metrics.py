"""Local ``MetricsPlugin`` — no metric capture in offline/test mode.

A counter that is never counted and a gauge that is never read: every
operation is a no-op, so tenant runs under the ``test``/``singlebox``
column behave exactly as they did before the metrics seam existed.
``time_block`` yields a :class:`contextlib.nullcontext`, so call-site
``with`` statements stay valid with nothing timed.

Paired with the community impl (``plugins.community.metrics``); the prod
implementation (a real registry + exposition) is selected by the corp
composition root.
"""
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext

from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.metrics import MetricLabels, MetricsPlugin
from agentclaw.community.plugins.local._mock_seam import MockSeam


@plugin_impl(mode=Mode.LOCAL, flavor=Flavor.NOOP, rationale="no metrics backend offline")
class NoopMetrics(MockSeam, MetricsPlugin):
    """Test/offline double: metric capture is unavailable."""

    def increment(self, name: str, labels: MetricLabels | None = None) -> None:
        return None

    def observe(self, name: str, value: float, labels: MetricLabels | None = None) -> None:
        return None

    def set_gauge(self, name: str, value: float, labels: MetricLabels | None = None) -> None:
        return None

    def time_block(
        self, name: str, labels: MetricLabels | None = None
    ) -> AbstractContextManager[None]:
        return nullcontext()