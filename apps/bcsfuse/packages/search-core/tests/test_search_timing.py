"""Timing must explain actual execution without leaking input or changing results."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import pytest
from search_core.api.errors import UnsupportedSearchOption
from search_core.api.models import SearchCommand, VerifiedIdentity
from search_core.application.search import CatalogSearchApplication
from search_core.core.execution import DeadlineExecutor
from search_core.core.observation import events
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.reranker import RerankResult
from search_core.plugin_api.vector_search_hit import VectorSearchHit


class Embedding:
    dimension = 2

    async def embed(self, query):
        await asyncio.sleep(0)
        return [1.0, 0.0]


class Index:
    async def keyword_search(self, *args):
        return [VectorSearchHit(id="a", score=2.0, payload={"content": "private card"})]

    async def vector_search(self, *args):
        return [VectorSearchHit(id="a", score=0.9, payload={"content": "private card"})]


class Reranker:
    async def rerank(self, *args):
        return [RerankResult("a", 0.8)]


def application(**kwargs):
    return CatalogSearchApplication(
        HybridRetrievalService(index=Index(), embedding=Embedding(), **kwargs)
    )


@pytest.fixture
def timing_logs(caplog):
    # Drain other tests' observations before inspecting this request.
    events.flush(force=True)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="search_core.events")

    def read():
        events.flush(force=True)
        return [
            json.loads(r.message)
            for r in caplog.records
            if r.name == "search_core.events"
            and json.loads(r.message)["event"] == "search_timing"
        ]

    return read


def test_success_has_stage_timings_and_no_query_or_card(timing_logs):
    result = asyncio.run(
        application(reranker=Reranker()).execute(
            SearchCommand("private query", 1, "search"),
            VerifiedIdentity("caller"),
        )
    )
    assert result.items[0].score == 0.8
    logs = timing_logs()
    assert sum(log["count"] for log in logs) == 1
    log = logs[-1]
    assert log["reason"] == "ok"
    sample = log["sample"]
    assert sample["request_id"]
    assert sample["result_count"] == 1
    assert {
        "validation",
        "embedding",
        "keyword",
        "vector",
        "recall_merge",
        "fusion",
        "reranker",
        "result_build",
    } <= sample["stages"].keys()
    assert "authorization" not in sample["stages"]
    for stage in sample["stages"].values():
        assert stage["elapsed_ms"] >= 0
    assert "private" not in json.dumps(logs)
    assert "caller" not in json.dumps(logs)


def test_progress_shows_input_branch_counts_and_returned_ids(caplog, timing_logs):
    app = CatalogSearchApplication(
        HybridRetrievalService(
            index=Index(), embedding=Embedding(), reranker=Reranker()
        ),
        filter_fields=frozenset({"resource_type"}),
        trace_id_provider=lambda: "incoming",
    )
    query = '查"账单" 13800138000'
    asyncio.run(
        app.execute(
            SearchCommand(query, 1, "search", {"resource_type": ("agent",)}),
            VerifiedIdentity("caller"),
        )
    )
    timing_logs()
    records = [
        json.loads(r.message) for r in caplog.records if r.name == "search_core.events"
    ]
    progress = {r["stage"]: r for r in records if r["event"] == "search_progress"}
    assert progress["received"]["query"] == '查"账单" [redacted]'
    assert progress["received"]["filters"] == {"resource_type": ["agent"]}
    assert progress["vector"]["raw_count"] == progress["keyword"]["raw_count"] == 1
    assert (
        progress["recall_merge"]["vector_count"]
        == progress["recall_merge"]["keyword_count"]
        == 1
    )
    assert progress["fusion"]["candidate_count"] == 1
    assert (
        progress["reranker"]["input_count"] == progress["reranker"]["result_count"] == 1
    )
    final = next(r for r in records if r["event"] == "search_steps")
    assert final["results"] == [
        {
            "rank": 1,
            "item_id": "a",
            "score": 0.8,
            "score_source": "reranker",
            "score_details": {
                "keyword": {"value": 2.0, "rank": 1},
                "vector": {"value": 0.9, "rank": 1},
                "rrf": {"value": 2 / 61, "rank": 1},
                "reranker": {"value": 0.8, "rank": 1},
            },
        }
    ]
    assert all(
        r["request_id"] == final["request_id"] and r["trace_id"] == "incoming"
        for r in progress.values()
    )
    assert final["timestamp"] >= progress["received"]["timestamp"]
    assert "private card" not in json.dumps(records)


@pytest.mark.parametrize("failure", [False, True])
def test_configured_disabled_branches_only_warn_on_actual_failure(
    failure, caplog, timing_logs
):
    class Ranker:
        async def rerank(self, *args):
            raise TimeoutError("private failure")

    app = application(
        vector_enabled=False,
        allow_partial=True,
        reranker=Ranker() if failure else None,
    )
    result = asyncio.run(
        app.execute(SearchCommand("bill", 1, "search"), VerifiedIdentity("caller"))
    )
    summaries = timing_logs()
    assert summaries[-1]["reason"] == ("degraded" if failure else "ok")
    records = [
        json.loads(r.message) for r in caplog.records if r.name == "search_core.events"
    ]
    warnings = [r for r in records if r["event"] == "search_degraded"]
    assert [r["reason"] for r in warnings] == (
        ["reranker_unavailable"] if failure else []
    )
    final = next(r for r in records if r["event"] == "search_steps")
    assert final["reasons"] == list(result.reasons)
    assert final["results"][0]["score_details"]["keyword"] == {"value": 2.0, "rank": 1}
    assert final["results"][0]["score_details"]["vector"] is None
    assert final["results"][0]["score_details"]["reranker"] is None


def test_trace_id_is_captured_before_background_log_flush(timing_logs):
    active_trace = ContextVar("caller_trace", default="")
    service = CatalogSearchApplication(
        HybridRetrievalService(index=Index(), embedding=Embedding()),
        trace_id_provider=active_trace.get,
    )
    previous = active_trace.set("upstream-trace")
    try:
        asyncio.run(
            service.execute(SearchCommand("q", 1, "search"), VerifiedIdentity("caller"))
        )
    finally:
        active_trace.reset(previous)
    assert timing_logs()[-1]["sample"]["trace_id"] == "upstream-trace"


def test_embedding_timeout_preserves_keyword_result_and_timing(timing_logs):
    class StuckEmbedding:
        async def embed(self, query):
            await asyncio.Event().wait()

    with ThreadPoolExecutor() as pool:
        app = CatalogSearchApplication(
            HybridRetrievalService(
                index=Index(),
                embedding=StuckEmbedding(),
                allow_partial=True,
                execution={
                    "embedding": DeadlineExecutor(
                        pool, name="embedding", timeout_seconds=0.01
                    )
                },
            )
        )
        result = asyncio.run(
            app.execute(SearchCommand("hidden", 1, "search"), VerifiedIdentity("x"))
        )
    assert result.items[0].item_id == "a"
    assert result.reasons == ("embedding_unavailable",)
    log = timing_logs()[-1]
    assert log["reason"] == "degraded"
    stages = log["sample"]["stages"]
    assert stages["embedding"]["outcome"] == "timeout"
    assert stages["embedding"]["elapsed_ms"] >= 5
    assert stages["keyword"]["outcome"] == "ok"
    assert stages["recall"]["outcome"] == "degraded"
    assert "vector" not in stages


def test_validation_failure_still_has_request_timing(timing_logs):
    with pytest.raises(UnsupportedSearchOption):
        asyncio.run(
            application().execute(
                SearchCommand("hidden", 1, "search", filters={"private": ("secret",)}),
                VerifiedIdentity("x"),
            )
        )
    sample = timing_logs()[-1]["sample"]
    assert sample["error_type"] == "UnsupportedSearchOption"
    assert set(sample["stages"]) == {"configuration", "validation"}
    assert "secret" not in json.dumps(sample)


def test_concurrent_requests_do_not_share_stages(timing_logs, caplog):
    async def run():
        await asyncio.gather(
            application(reranker=Reranker()).execute(
                SearchCommand("x", 1, "search"), VerifiedIdentity("x")
            ),
            application(vector_enabled=False).execute(
                SearchCommand("y", 1, "search"), VerifiedIdentity("y")
            ),
        )

    asyncio.run(run())
    timing_logs()
    records = [
        json.loads(r.message) for r in caplog.records if r.name == "search_core.events"
    ]
    samples = [r for r in records if r["event"] == "search_steps"]
    assert len(samples) == 2
    assert len({s["request_id"] for s in samples}) == 2
    assert sorted("embedding" in s["stage_ms"] for s in samples) == [False, True]
    assert sorted("reranker" in s["stage_ms"] for s in samples) == [False, True]


def test_cache_hit_does_not_replay_old_stage_timings(timing_logs, caplog):
    from search_core.application.cache import (
        CachedEmbedding,
        CachedRetrieval,
        SearchCaches,
    )
    from search_core.application.emergency import CachePolicy, SearchCachePolicy

    state = SearchCaches(
        result_entries=10,
        result_bytes=100000,
        embedding_entries=10,
        embedding_bytes=100000,
    )
    result_cache, embedding_cache, _ = state.configure(
        SearchCachePolicy(
            result=CachePolicy(ttl_ms=10000), query_embedding=CachePolicy(ttl_ms=10000)
        )
    )
    retrieval = HybridRetrievalService(
        index=Index(), embedding=CachedEmbedding(Embedding(), embedding_cache, "test/2")
    )
    app = CatalogSearchApplication(CachedRetrieval(retrieval, result_cache, state))
    command, identity = (
        SearchCommand("secret", 1, "search"),
        VerifiedIdentity("x"),
    )
    first = asyncio.run(app.execute(command, identity))
    before = len(timing_logs())
    second = asyncio.run(app.execute(command, identity))
    sample = timing_logs()[before:][-1]["sample"]
    assert second == first
    assert sample["stages"]["result_cache_read"]["cache"] == "hit"
    assert not {"embedding", "vector", "keyword", "reranker"} & sample["stages"].keys()
    steps = [
        json.loads(r.message)
        for r in caplog.records
        if r.name == "search_core.events"
        and json.loads(r.message)["event"] == "search_steps"
    ]
    assert [record["result_cache"] for record in steps] == ["miss", "hit"]
    assert steps[0]["request_id"] != steps[1]["request_id"] == sample["request_id"]
    assert steps[1]["stage_ms"] == {
        name: stage["elapsed_ms"] for name, stage in sample["stages"].items()
    }
    assert "secret" not in json.dumps(steps)
    caplog.set_level(logging.INFO, logger="search_core.application.cache")
    state.log_stats()
    baseline = len(caplog.records)
    state.log_stats()
    assert len(caplog.records) == baseline
    result_cache.get(b"missing")
    state.log_stats()
    assert len(caplog.records) == baseline + 1
    assert json.loads(caplog.records[-1].message)["cache"] == "result"
    result_cache.clear()
    before = len(timing_logs())
    asyncio.run(app.execute(command, identity))
    sample = timing_logs()[before:][-1]["sample"]
    assert sample["stages"]["embedding_cache_read"]["cache"] == "hit"
    assert "embedding_cache_write" not in sample["stages"]


def test_cancelled_request_records_elapsed_time(timing_logs):
    started = asyncio.Event()

    class PendingIndex(Index):
        async def vector_search(self, *args):
            started.set()
            await asyncio.Event().wait()

    async def run():
        app = CatalogSearchApplication(
            HybridRetrievalService(index=PendingIndex(), embedding=Embedding())
        )
        task = asyncio.create_task(
            app.execute(SearchCommand("secret", 1, "search"), VerifiedIdentity("x"))
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    log = timing_logs()[-1]
    assert log["reason"] == "cancelled"
    assert log["sample"]["stages"]["vector"]["outcome"] == "cancelled"


def test_timing_logs_are_bounded_and_keep_slowest_request(caplog, monkeypatch):
    from search_core.core.observation import EventSummaries, logger

    caplog.set_level(logging.INFO, logger="search_core.events")
    summaries = EventSummaries(interval=3600)

    def blocked_sink(*args):
        raise AssertionError("request must not write the log sink")

    with monkeypatch.context() as patch:
        patch.setattr(logger, "log", blocked_sink)
        for elapsed in (1, 100, *([2] * 998)):
            summaries.record(
                "search_timing",
                "request",
                "ok",
                elapsed_ms=elapsed,
                stages={"keyword": {"elapsed_ms": elapsed}},
            )
    summaries.flush(force=True)
    logs = [
        json.loads(r.message) for r in caplog.records if r.name == "search_core.events"
    ]
    assert len(logs) <= 2
    assert sum(log["count"] for log in logs) == 1000
    assert logs[-1]["sample"]["stages"]["keyword"]["elapsed_ms"] == 100
    assert logs[-1]["max_ms"] == 100
    assert 2 <= logs[-1]["avg_ms"] <= 2.1


def test_default_candidates_metadata_cache_and_db_failure(
    tmp_path, timing_logs, monkeypatch
):
    from search_core.application.cache import SearchCaches
    from search_core.application.card_metadata import CardMetadataRetrieval
    from search_core.application.catalog_retrieval import CatalogRetrieval
    from search_core.application.default_candidates import DefaultCandidates
    from search_core.application.emergency import SearchCachePolicy
    from search_core.catalog.cards import CardSnapshot
    from search_core.providers.sql_catalog import sqlite_catalog_repository

    repository = sqlite_catalog_repository(str(tmp_path / "cards.db"))
    repository.save(
        CardSnapshot(
            agent_id="a",
            source_version="1",
            source_card={"name": "private name", "description": "private description"},
        ),
        0,
    )

    class Catalog:
        def eligible(self, agent_id, filters):
            return agent_id == "a"

    state = SearchCaches(
        result_entries=10,
        result_bytes=100000,
        embedding_entries=10,
        embedding_bytes=100000,
    )
    _, _, metadata_cache = state.configure(SearchCachePolicy())
    with ThreadPoolExecutor() as pool:
        execution = DeadlineExecutor(pool, name="card_metadata", timeout_seconds=1)
        retrieval = CardMetadataRetrieval(
            DefaultCandidates(
                repository, Catalog(), state.candidate_ids, state, execution
            ),
            repository,
            metadata_cache,
            state,
            execution,
        )
        app = CatalogSearchApplication(CatalogRetrieval(retrieval, Catalog()))
        command, identity = (
            SearchCommand("secret", 1, "search"),
            VerifiedIdentity("x"),
        )
        first = asyncio.run(app.execute(command, identity))
        first_stages = timing_logs()[-1]["sample"]["stages"]
        assert first.items[0].name == "private name"
        assert first_stages["card_metadata"]["cache_misses"] == 1
        assert first_stages["card_fetch"]["outcome"] == "ok"
        before = len(timing_logs())
        second = asyncio.run(app.execute(command, identity))
        sample = timing_logs()[before:][-1]["sample"]
        assert first == second
        assert sample["stages"]["default_candidates"]["cache"] == "hit"
        assert sample["stages"]["card_metadata"]["cache_hits"] == 1
        assert "card_fetch" not in sample["stages"]
        assert (
            not {"embedding", "vector", "keyword", "reranker"} & sample["stages"].keys()
        )
        assert sample["stages"]["eligibility"]["result_count"] == 1

        def broken(ids):
            raise RuntimeError("private database error")

        monkeypatch.setattr(repository, "get_many_cards", broken)
        metadata_cache.clear()
        before = len(timing_logs())
        result = asyncio.run(app.execute(command, identity))
        sample = timing_logs()[before:][-1]["sample"]
        assert not result.items
        assert "card_metadata_unavailable" in result.reasons
        assert sample["stages"]["card_metadata"]["outcome"] == "unavailable"
        assert sample["stages"]["card_fetch"]["outcome"] == "dependency_error"
        assert "private" not in json.dumps(sample)
