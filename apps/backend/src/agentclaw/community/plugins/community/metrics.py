"""Community ``MetricsPlugin`` — a bounded in-process registry, no exporter.

A real, deployable metrics impl for the open-source build: there is no
collector and no exposition endpoint, so the registry itself is the
deliverable. Every operation folds into a per-(name, labels) summary
cell — counters accumulate, observes keep count/sum/min/max, gauges keep
the last value — under one lock, all readable through :meth:`snapshot`
for diagnostics and tests.

Depends on no metrics SDK. Not a ``MockSeam`` subclass: this is a real
impl bound directly by ``CommunityMetricsModule``.
"""
from __future__ import annotations

import threading
import time
from contextlib import AbstractContextManager, contextmanager
from typing import Iterator

from agentclaw.community.plugin_api.metrics import MetricLabels, MetricsPlugin


def _label_key(labels: MetricLabels | None) -> tuple[tuple[str, str], ...]:
    """Freeze ``labels`` into a hashable, sorted (key, str(value)) tuple.

    Label values are coerced via ``str()`` — the Protocol's declared answer
    to unhashable or exotic values, so a caller passing a list never fails
    the run. Sorting makes label sets with identical content collide onto
    one cell regardless of dict insertion order.
    """
    items = labels.items() if labels else ()
    return tuple(sorted((str(k), str(v)) for k, v in items))


class _ObserveSummary:
    """count/sum/min/max fold for one (metric, labels) cell.

    Deliberately no per-sample storage: a slice loop can observe many times
    a second for hours, and the summary is the bounded representation of
    that series (percentiles are derivable off-box from many cells).
    """

    __slots__ = ("count", "sum", "min", "max")

    def __init__(self, first: float) -> None:
        self.count = 1
        self.sum = first
        self.min = first
        self.max = first

    def add(self, value: float) -> None:
        self.count += 1
        self.sum += value
        if value < self.min:
            self.min = value
        if value > self.max:
            self.max = value

    def as_dict(self) -> dict[str, float]:
        return {"count": self.count, "sum": self.sum, "min": self.min, "max": self.max}


class CommunityMetrics(MetricsPlugin):
    """Real community metrics: in-proc summaries, snapshot-readable."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._observes: dict[tuple[str, tuple[tuple[str, str], ...]], _ObserveSummary] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def increment(self, name: str, labels: MetricLabels | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            self._counters[(name, key)] = self._counters.get((name, key), 0.0) + 1.0

    def observe(self, name: str, value: float, labels: MetricLabels | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            cell = self._observes.get((name, key))
            if cell is None:
                self._observes[(name, key)] = _ObserveSummary(float(value))
            else:
                cell.add(float(value))

    def set_gauge(self, name: str, value: float, labels: MetricLabels | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            self._gauges[(name, key)] = float(value)

    @contextmanager
    def time_block(
        self, name: str, labels: MetricLabels | None = None
    ) -> Iterator[None]:
        time_block_start = time.monotonic()
        try:
            yield
        finally:
            self.observe(name, time.monotonic() - time_block_start, labels)

    def snapshot(self) -> dict[str, dict[str, dict[str, float]]]:
        """A point-in-time copy of the whole registry, grouped by operation.

        ``{"increment": {(name, labels): value}, "observe": {…summary…},
        "set_gauge": {…last value…}}`` — the only intended read surface.
        """
        with self._lock:
            return {
                "increment": dict(self._counters),
                "observe": {
                    key: cell.as_dict() for key, cell in self._observes.items()
                },
                "set_gauge": dict(self._gauges),
            }