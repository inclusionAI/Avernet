"""MetricsPlugin — operational metrics capability.

The community tree has no metrics backend: no registry, no exposition
endpoint, no vendor SDK. Operational modules that need to count things
nevertheless (agentcompute's D39 surface is the first) must not grow
their own exporters. This Protocol moves that behind a capability, the
same way ``plugin_api.tracer`` moved tracing:

- the runtime (worker, slice loop, admission) depends on the Protocol
  only, so neutral layers stay vendor-free;
- each deployment profile picks the impl: a real registry in prod,
  anything cheap enough locally.

Every operation is diagnostic-first like the tracer's: **never raise**.
When a caller's label value is not one of the legal values declared in
the metric's owning module, it is coerced via ``str()`` — a metric must
never fail a run. The label-map is a dict rather than kwargs so id
(L18/D18 typed) domain values do not have to be inspected here.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Mapping, Protocol

from agentclaw.community.plugin_api.base import Plugin

#: Per-label values for a metric call. Keys are the metric's declared
#: dimensions (e.g. ``tenant``, ``status``, ``upstream``); values are
#: scalars coerced by the implementation. Not typed further here: the
#: metric surface is deliberately unopinionated so implementations can
#: stay pass-through and vendors stay swappable.
MetricLabels = Mapping[str, object]


class MetricsPlugin(Plugin, Protocol):
    """Per-runtime operational metrics."""

    def increment(self, name: str, labels: MetricLabels | None = None) -> None:
        """Add 1 to counter ``name``.

        Used for event counts: claims, reclaims, admission rejects,
        STUCK transitions, LLM errors by class.
        """
        ...

    def observe(self, name: str, value: float, labels: MetricLabels | None = None) -> None:
        """Record ``value`` in histogram ``name``.

        Used for distributions: slice and run durations, queue depth
        samples, token counts per tenant.
        """
        ...

    def set_gauge(self, name: str, value: float, labels: MetricLabels | None = None) -> None:
        """Set gauge ``name`` to ``value`` (absolute, not delta).

        Used for point-in-time levels: pending jobs, in-flight slices.
        """
        ...

    def time_block(
        self, name: str, labels: MetricLabels | None = None
    ) -> AbstractContextManager[None]:
        """Time the wrapped block into histogram ``name``.

        Declared as a context manager (like ``TracerPlugin.trace_scope``)
        so an impl may choose a generator or a hand-written class. Must
        not raise on enter/exit — an unmeasured block still has to run.
        """
        ...