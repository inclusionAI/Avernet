"""Regression tests for the high-concurrency search metric lifecycle."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator

import prometheus_client
from prometheus_client import CollectorRegistry, Histogram


@contextmanager
def _fresh_metric_registry(monkeypatch) -> Iterator[None]:
    """Isolate Prometheus registration from tests executed in the same process."""

    from app.infra.public.observability import search_dependency_metrics as metrics

    monkeypatch.setattr(prometheus_client, "REGISTRY", CollectorRegistry())
    monkeypatch.setattr(metrics, "_METRICS", None)

    class SlowHistogram(Histogram):
        """Force overlapping initialization so the race is deterministic."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            import time

            time.sleep(0.05)
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(prometheus_client, "Histogram", SlowHistogram)
    yield


def test_concurrent_metric_initialization_is_safe(monkeypatch) -> None:
    """Concurrent workers must initialize one metric set, not duplicate collectors."""

    with _fresh_metric_registry(monkeypatch):
        from typing import Any

        from app.infra.public.observability import search_dependency_metrics as metrics

        worker_count = 24
        barrier = __import__("threading").Barrier(worker_count)

        def initialize(_worker: Any = None) -> int:
            barrier.wait()
            return id(metrics._get_metrics())

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            metric_ids = list(executor.map(initialize, range(worker_count)))

        assert len(metric_ids) == worker_count
        assert len(set(metric_ids)) == 1
        assert metrics._METRICS is not None
        assert metrics._METRICS["embedding_calls"] is not None
        assert metrics._METRICS["qdrant_duration"] is not None
