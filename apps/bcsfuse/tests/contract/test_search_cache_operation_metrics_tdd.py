"""Response-cache metrics must stay separated by serving operation."""

from __future__ import annotations

from prometheus_client import REGISTRY


def _metric(name: str, operation: str) -> float:
    labels = {"api_version": "v1", "operation": operation}
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else float(value)


def _cache():
    from app.application.agent_search_cache import AgentSearchResultCache

    return AgentSearchResultCache(
        index_version_provider=lambda: 7,
        max_entries=10,
        ttl_seconds=30.0,
    )


def test_l1_cache_metrics_split_search_recommendation_and_candidate() -> None:
    from app.infra.public.observability.search_result_cache_metrics import (
        record_search_result_cache_miss,
        set_search_result_cache_entries,
    )

    cache = _cache()

    for operation in (
        "v1_search_response",
        "v1_recommendation_response",
        "candidate",
    ):
        key = f"metrics-operation-{operation}"
        cache.store(key, b"result", operation=operation)
        assert cache.get(key, operation=operation) == b"result"

    assert _metric(
        "bcsfuse_search_result_cache_hits_total", "v1_search_response"
    ) >= 1
    assert _metric(
        "bcsfuse_search_result_cache_hits_total", "v1_recommendation_response"
    ) >= 1
    assert _metric(
        "bcsfuse_search_result_cache_hits_total", "candidate"
    ) >= 1

    # Preserve the existing v2 application cache contract while adding an
    # explicit operation dimension.
    record_search_result_cache_miss("v2", operation="v2_search")
    set_search_result_cache_entries(1, "v2", operation="v2_search")
    assert REGISTRY.get_sample_value(
        "bcsfuse_search_result_cache_misses_total",
        {"api_version": "v2", "operation": "v2_search"},
    ) is not None
