"""Degradation is opt-in; deadlines cancel queued work without invented quotas."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from search_core.api.errors import SearchUnavailable
from search_core.api.models import RetrievalRequest
from search_core.core.execution import DeadlineExecutor
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.vector_search_hit import VectorSearchHit


class Index:
    def keyword_search(self, *args):
        return [VectorSearchHit(id="a", score=1, payload={"name": "A"})]

    def vector_search(self, *args):
        return self.keyword_search()


class Embedding:
    def embed(self, text):
        return [1.0, 0.0]


class BrokenEmbedding:
    def embed(self, text):
        raise RuntimeError("private details")


def test_embedding_failure_retains_keyword_only_when_opted_in():
    async def run():
        service = HybridRetrievalService(index=Index(), embedding=BrokenEmbedding())
        with pytest.raises(SearchUnavailable):
            await service.retrieve(RetrievalRequest("x", 1, {}, None))
        service = HybridRetrievalService(
            index=Index(), embedding=BrokenEmbedding(), allow_partial=True
        )
        result = await service.retrieve(RetrievalRequest("x", 1, {}, None))
        assert result.items[0].item_id == "a"
        assert result.status == "degraded" and result.reasons == (
            "embedding_unavailable",
        )

    asyncio.run(run())


def test_stages_expose_one_query_vector_and_reranker_failure_keeps_fused_order():
    class CountingEmbedding(Embedding):
        calls = 0

        def embed(self, text):
            self.calls += 1
            return super().embed(text)

    class Reranker:
        def rerank(self, *args):
            raise RuntimeError("quota")

    async def run():
        embedding = CountingEmbedding()
        service = HybridRetrievalService(
            index=Index(), embedding=embedding, reranker=Reranker(), allow_partial=True
        )
        request = RetrievalRequest("x", 1, {}, None)
        recall = await service.recall(request)
        assert recall.query_vector == (1.0, 0.0)
        result = await service.rank(request, recall)
        assert embedding.calls == 1
        assert result.items[0].score_source == "rrf"
        assert result.reasons == ("reranker_unavailable",)

    asyncio.run(run())


def test_more_than_64_calls_wait_for_execution_instead_of_local_rejection():
    import time

    async def run(executor):
        call = DeadlineExecutor(executor, name="embedding", timeout_seconds=2)

        def work(value):
            time.sleep(0.002)
            return value

        assert await asyncio.gather(*(call.call(work, i) for i in range(128))) == list(
            range(128)
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        asyncio.run(run(executor))


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_cancellation_prevents_queued_obsolete_model_calls(cancel):
    entered, release = Event(), Event()
    obsolete_calls = []

    def blocked():
        entered.set()
        release.wait(2)

    async def run(executor):
        call = DeadlineExecutor(executor, name="embedding", timeout_seconds=0.03)
        task = asyncio.create_task(call.call(lambda: obsolete_calls.append(1)))
        if cancel:
            await asyncio.sleep(0.005)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
        release.set()
        assert await call.call(lambda: "recovered") == "recovered"
        assert obsolete_calls == []

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(blocked)
        assert entered.wait(1)
        try:
            asyncio.run(run(executor))
        finally:
            release.set()


def test_provider_quota_errors_do_not_trigger_retries_or_a_local_circuit():
    calls = []

    class QuotaError(Exception):
        status_code = 429

    def quota():
        calls.append(1)
        raise QuotaError()

    async def run(executor):
        call = DeadlineExecutor(executor, name="reranker", timeout_seconds=1)
        for _ in range(5):
            with pytest.raises(QuotaError):
                await call.call(quota)
        assert calls == [1] * 5
        assert await call.call(lambda: "recovered") == "recovered"

    with ThreadPoolExecutor() as executor:
        asyncio.run(run(executor))


def test_no_success_when_all_recall_paths_fail_and_no_hidden_scope_bypass():
    class BrokenIndex(Index):
        def keyword_search(self, *args):
            raise RuntimeError("down")

    async def run():
        service = HybridRetrievalService(
            index=BrokenIndex(), embedding=Embedding(), allow_partial=True
        )
        request = RetrievalRequest("x", 1, {}, None)
        recall = await service.recall(request)
        assert not recall.available
        assert set(recall.reasons) == {"vector_unavailable", "keyword_unavailable"}
        with pytest.raises(SearchUnavailable):
            await service.rank(request, recall)
        service = HybridRetrievalService(
            index=Index(), embedding=BrokenEmbedding(), allow_partial=True
        )
        with pytest.raises(SearchUnavailable, match="unauthorized"):
            await service.retrieve(RetrievalRequest("x", 1, {}, frozenset({"b"})))

    asyncio.run(run())
