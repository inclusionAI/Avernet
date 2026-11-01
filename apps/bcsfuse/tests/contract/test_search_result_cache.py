"""Bounded v2 result caching keyed by request, authority, and index revision."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from prometheus_client import REGISTRY
from search_core.api.errors import SearchOverloaded, SearchUnavailable
from search_core.api.models import SearchCommand, VerifiedIdentity

from app.application.search.service import TcSearchApplication
from app.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from app.domain.models.retrieval_mode import RetrievalMode


class Candidates:
    def __init__(self, *, fail=False, metadata=None, blocking=False):
        self.fail = fail
        self.metadata = metadata or {}
        self.calls = []
        self.blocking = blocking
        self.entered = threading.Event()
        self.release = threading.Event()

    def recommend(self, **kwargs):
        self.calls.append(kwargs)
        if self.blocking:
            self.entered.set()
            self.release.wait(timeout=3)
        if self.fail:
            raise RuntimeError("candidate service unavailable")
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            mode=kwargs["mode"],
            recommendations=[
                CandidateRecommendation(
                    profile_key="bot:public:profile",
                    worker_id="bot:public",
                    score=0.8,
                    short_profile="TC profile",
                )
            ],
            total_candidates=1,
            selected_candidates=1,
            metadata=self.metadata,
        )


class Version:
    def __init__(self, version=1):
        self.version = version

    def __call__(self):
        return self.version


def search(query="星河 数据分析", *, limit=5, operation="search", filters=None, extensions=None):
    return SearchCommand(
        query,
        limit,
        operation,
        filters=filters or {},
        extensions=extensions or {},
    )


def identity(subject="caller", allowed=None):
    return VerifiedIdentity(subject, allowed)


def service(candidates, **kwargs):
    return TcSearchApplication(candidates, executor=ThreadPoolExecutor(max_workers=2), **kwargs)


def metric(name, labels):
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else float(value)


@pytest.mark.parametrize("cache_enabled", [True, False])
def test_cache_hits_repeat_only_when_a_version_provider_is_supplied(cache_enabled):
    candidates = Candidates()
    kwargs = {"index_version_provider": Version()} if cache_enabled else {}
    application = service(candidates, **kwargs)

    try:
        for _ in range(2):
            result = asyncio.run(
                application.execute(search(), identity())
            )
        assert result.status == "ok"
    finally:
        application._executor.shutdown()

    assert len(candidates.calls) == (1 if cache_enabled else 2)
    assert application.result_cache_stats["hits"] == (1 if cache_enabled else 0)
    assert application.result_cache_stats["misses"] == (1 if cache_enabled else 0)


def test_cache_key_is_scoped_by_identity_filters_and_rerank_option():
    candidates = Candidates()
    version = Version()
    application = service(candidates, index_version_provider=version, max_entries=10)

    try:
        asyncio.run(application.execute(search(), identity("caller-a", None)))
        asyncio.run(application.execute(search(), identity("caller-b", None)))
        asyncio.run(
            application.execute(search(), identity("caller-a", frozenset({"other"})))
        )
        asyncio.run(application.execute(search(filters={"runtime_state": ("online",)}), identity()))
        asyncio.run(
            application.execute(search(extensions={"enable_rerank": False}), identity())
        )
    finally:
        application._executor.shutdown()

    assert len(candidates.calls) == 5
    assert application.result_cache_stats["hits"] == 0


def test_index_version_change_invalidates_cached_result():
    candidates = Candidates()
    version = Version()
    application = service(candidates, index_version_provider=version)

    try:
        asyncio.run(application.execute(search(), identity()))
        version.version = 2
        asyncio.run(application.execute(search(), identity()))
    finally:
        application._executor.shutdown()

    assert len(candidates.calls) == 2


def test_degraded_and_failed_results_are_not_cached():
    degraded = Candidates(metadata={"semantic_unavailable": True})
    failing = Candidates(fail=True)
    degraded_app = service(degraded, index_version_provider=Version())
    failing_app = service(failing, index_version_provider=Version())

    try:
        first = asyncio.run(degraded_app.execute(search(), identity()))
        second = asyncio.run(degraded_app.execute(search(), identity()))
        assert first.status == second.status == "degraded"
        assert len(degraded.calls) == 2

        with pytest.raises(SearchUnavailable):
            asyncio.run(failing_app.execute(search(), identity()))
        with pytest.raises(SearchUnavailable):
            asyncio.run(failing_app.execute(search(), identity()))
        assert len(failing.calls) == 2
    finally:
        degraded_app._executor.shutdown()
        failing_app._executor.shutdown()


def test_ttl_and_lru_bound_the_cache():
    class Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    candidates = Candidates()
    clock = Clock()
    application = service(
        candidates,
        index_version_provider=Version(),
        max_entries=1,
        ttl_seconds=10,
        clock=clock,
    )

    try:
        asyncio.run(application.execute(search("first"), identity()))
        clock.now = 10
        asyncio.run(application.execute(search("first"), identity()))
        assert len(candidates.calls) == 2

        asyncio.run(application.execute(search("second"), identity()))
        clock.now = 11
        asyncio.run(application.execute(search("first"), identity()))
        assert len(candidates.calls) == 4
    finally:
        application._executor.shutdown()


def test_cache_hits_do_not_consume_the_last_search_capacity_slot():
    candidates = Candidates(blocking=True)
    application = service(candidates, index_version_provider=Version(), max_inflight=1)
    command = search("first")
    caller = identity()

    async def exercise():
        first = asyncio.create_task(application.execute(command, caller))
        assert await asyncio.to_thread(candidates.entered.wait, 2)

        overloaded = asyncio.create_task(
            application.execute(search("second"), caller)
        )
        with pytest.raises(SearchOverloaded):
            await overloaded
        candidates.release.set()
        await first
        # The cached request must bypass capacity admission.
        assert await application.execute(command, caller)

    try:
        asyncio.run(exercise())
    finally:
        application._executor.shutdown()
    assert len(candidates.calls) == 1


def test_identical_concurrent_requests_share_one_backend_call():
    candidates = Candidates(blocking=True)
    application = service(candidates, index_version_provider=Version(), max_inflight=2)
    command = search("single")

    async def exercise():
        first = asyncio.create_task(application.execute(command, identity()))
        second = asyncio.create_task(application.execute(command, identity()))
        assert await asyncio.to_thread(candidates.entered.wait, 2)
        await asyncio.to_thread(candidates.release.set)
        return await asyncio.gather(first, second)

    try:
        first_result, second_result = asyncio.run(exercise())
    finally:
        application._executor.shutdown()

    assert first_result.items == second_result.items
    assert len(candidates.calls) == 1


def test_result_cache_metrics_are_exported():
    candidates = Candidates()
    application = service(candidates, index_version_provider=Version())
    try:
        asyncio.run(application.execute(search("metrics-query"), identity()))
        asyncio.run(application.execute(search("metrics-query"), identity()))
    finally:
        application._executor.shutdown()

    assert metric(
        "bcsfuse_search_result_cache_hits_total",
        {"api_version": "v2", "operation": "v2_search"}
    ) >= 1
    assert metric(
        "bcsfuse_search_result_cache_misses_total",
        {"api_version": "v2", "operation": "v2_search"}
    ) >= 1
    assert metric(
        "bcsfuse_search_result_cache_entries",
        {"api_version": "v2", "operation": "v2_search"}
    ) >= 1
