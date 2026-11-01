"""Latency metrics for the shared application search stages."""

from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterator


_METRICS: dict[str, object] | None = None


def _get_metrics() -> dict[str, object]:
    global _METRICS
    if _METRICS is not None:
        return _METRICS

    from prometheus_client import Histogram

    _METRICS = {
        "duration": Histogram(
            "bcsfuse_search_stage_duration_seconds",
            "Latency of the shared search and recommendation stages",
            ["operation", "stage"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
        )
    }
    return _METRICS


@contextmanager
def search_stage_metrics(operation: str, stage: str) -> Iterator[None]:
    """Record one core-stage wall-clock duration, including offload wait."""

    metric = _get_metrics()["duration"].labels(operation=operation, stage=stage)
    started_at = perf_counter()
    try:
        yield
    finally:
        metric.observe(perf_counter() - started_at)


__all__ = ["search_stage_metrics"]
