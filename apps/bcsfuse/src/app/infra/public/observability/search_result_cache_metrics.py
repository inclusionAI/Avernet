"""Prometheus metrics for the bounded v2 result cache."""

from __future__ import annotations


_RESULT_CACHE_METRICS: dict[str, object] | None = None


def _metrics() -> dict[str, object]:
    global _RESULT_CACHE_METRICS
    if _RESULT_CACHE_METRICS is not None:
        return _RESULT_CACHE_METRICS

    from prometheus_client import Counter, Gauge

    labels = ["api_version", "operation"]
    _RESULT_CACHE_METRICS = {
        "hits": Counter(
            "bcsfuse_search_result_cache_hits_total",
            "Successful v2 result cache hits",
            labels,
        ),
        "misses": Counter(
            "bcsfuse_search_result_cache_misses_total",
            "v2 result cache misses that require candidate execution",
            labels,
        ),
        "evictions": Counter(
            "bcsfuse_search_result_cache_evictions_total",
            "Entries removed by v2 cache expiry or LRU capacity",
            labels,
        ),
        "entries": Gauge(
            "bcsfuse_search_result_cache_entries",
            "Entries currently held by the v2 result cache",
            labels,
        ),
    }
    return _RESULT_CACHE_METRICS


def record_search_result_cache_hit(
    api_version: str = "v2", *, operation: str = "v2_search"
) -> None:
    _metrics()["hits"].labels(api_version=api_version, operation=operation).inc()


def record_search_result_cache_miss(
    api_version: str = "v2", *, operation: str = "v2_search"
) -> None:
    _metrics()["misses"].labels(api_version=api_version, operation=operation).inc()


def record_search_result_cache_eviction(
    api_version: str = "v2", *, operation: str = "v2_search"
) -> None:
    _metrics()["evictions"].labels(
        api_version=api_version, operation=operation
    ).inc()


def set_search_result_cache_entries(
    entries: int,
    api_version: str = "v2",
    *,
    operation: str = "v2_search",
) -> None:
    _metrics()["entries"].labels(
        api_version=api_version, operation=operation
    ).set(entries)
