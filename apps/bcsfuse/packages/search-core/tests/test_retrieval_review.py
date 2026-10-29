"""Slow dependencies time out while successful recall can still return."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from search_core.api.models import RetrievalRequest
from search_core.core.execution import DeadlineExecutor
from search_core.core.retrieval import HybridRetrievalService
from test_retrieval_resilience import Embedding, Index


def test_keyword_results_survive_slow_vector_search():
    entered, release = Event(), Event()

    class SlowVector(Index):
        def vector_search(self, *args):
            entered.set()
            release.wait(2)
            return super().vector_search(*args)

    async def run(executor):
        pools = {
            name: DeadlineExecutor(executor, timeout_seconds=0.02, name=name)
            for name in ("vector", "keyword")
        }
        service = HybridRetrievalService(
            index=SlowVector(),
            embedding=Embedding(),
            allow_partial=True,
            execution=pools,
        )
        try:
            result = await service.retrieve(RetrievalRequest("x", 1, {}, None))
            assert entered.is_set()
            assert result.status == "degraded"
            assert result.reasons == ("vector_unavailable",)
            assert result.items[0].item_id == "a"
            again = await service.retrieve(RetrievalRequest("x", 1, {}, None))
            assert again.items and again.status == "degraded"
        finally:
            release.set()

    with ThreadPoolExecutor() as executor:
        asyncio.run(run(executor))


def test_budget_returns_keyword_without_waiting_for_slow_embedding_or_rerank():
    import time

    from search_core.core.observation import request_budget

    class SlowEmbedding:
        def embed(self, text):
            time.sleep(0.15)
            return [1.0, 0.0]

    class NoBudgetReranker:
        def rerank(self, *args):
            raise AssertionError("must skip when budget exhausted")

    async def run(executor):
        pools = {
            name: DeadlineExecutor(executor, timeout_seconds=1, name=name)
            for name in ("embedding", "keyword", "reranker")
        }
        service = HybridRetrievalService(
            index=Index(),
            embedding=SlowEmbedding(),
            reranker=NoBudgetReranker(),
            allow_partial=True,
            execution=pools,
        )
        started = time.monotonic()
        with request_budget(0.03):
            result = await service.retrieve(RetrievalRequest("x", 1, {}, None))
        assert time.monotonic() - started < 0.12
        assert result.items[0].score_source == "rrf"
        assert set(result.reasons) == {
            "embedding_unavailable",
            "reranker_unavailable",
        }

    with ThreadPoolExecutor() as executor:
        asyncio.run(run(executor))


def test_failure_logs_count_every_event_and_do_not_log_queries(caplog):
    import json

    from search_core.core.observation import EventSummaries

    summaries = EventSummaries(interval=60)
    for _ in range(100):
        summaries.record("search_stage_failure", "vector", "timeout", elapsed_ms=20)
    summaries.flush(force=True)
    records = [
        json.loads(r.message) for r in caplog.records if r.name == "search_core.events"
    ]
    assert len(records) == 2
    assert sum(r["count"] for r in records) == 100
    assert records[-1]["stage"] == "vector"


def test_index_read_lock_has_finite_wait_when_writer_is_stuck():
    import threading

    import pytest
    from search_core.providers.index_lock import IndexLock

    lock = IndexLock(concurrent_reads=True)
    entered, release = Event(), Event()

    def writer():
        with lock:
            entered.set()
            release.wait(2)

    thread = threading.Thread(target=writer)
    thread.start()
    entered.wait(1)
    try:
        with pytest.raises(TimeoutError), lock.read(timeout_seconds=0.02):
            raise AssertionError("writer still owns index")
    finally:
        release.set()
        thread.join()


def test_record_never_writes_slow_log_sink(monkeypatch):
    from search_core.core.observation import EventSummaries, logger

    def blocked_sink(*args):
        raise AssertionError("request path must not write to sink")

    with monkeypatch.context() as patch:
        patch.setattr(logger, "warning", blocked_sink)
        summaries = EventSummaries()
        for _ in range(1000):
            summaries.record("search_stage_failure", "embedding", "timeout")
    summaries.flush(force=True)


def test_wrapped_model_timeout_and_quota_have_distinct_log_reasons():
    from search_core.core.observation import failure_reason

    try:
        try:
            raise TimeoutError("private text")
        except TimeoutError as cause:
            raise RuntimeError("wrapper") from cause
    except RuntimeError as error:
        assert failure_reason(error) == "timeout"

    class Quota(Exception):
        status_code = 429

    assert failure_reason(Quota()) == "quota_exceeded"
