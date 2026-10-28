"""Degradation is opt-in; bounded physical calls survive request timeouts."""

import asyncio
from threading import Event

import pytest
from search_core.api.errors import SearchOverloaded, SearchUnavailable
from search_core.api.models import RetrievalRequest
from search_core.core.execution import ExecutionPool
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


def test_physical_permit_not_released_on_timeout_or_request_cancel():
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        release.wait(2)
        return 1

    async def run():
        pool = ExecutionPool(workers=1, timeout_seconds=0.02, failure_threshold=10)
        try:
            with pytest.raises(TimeoutError):
                await pool.call(blocked)
            assert entered.is_set()
            with pytest.raises(SearchOverloaded):
                await pool.call(lambda: 2)
            release.set()
            for _ in range(100):
                if pool.inflight == 0:
                    break
                await asyncio.sleep(0.002)
            assert await pool.call(lambda: 2) == 2
            release.clear()
            task = asyncio.create_task(pool.call(blocked))
            while pool.inflight == 0:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(SearchOverloaded):
                await pool.call(lambda: 3)
        finally:
            release.set()
            pool.close()

    asyncio.run(run())


def test_circuit_opens_after_failures_and_recovers_after_cooldown():
    def broken():
        raise RuntimeError("down")

    async def run():
        pool = ExecutionPool(
            workers=1, timeout_seconds=1, failure_threshold=1, cooldown_seconds=0.02
        )
        try:
            with pytest.raises(RuntimeError):
                await pool.call(broken)
            with pytest.raises(SearchUnavailable):
                await pool.call(lambda: 1)
            await asyncio.sleep(0.03)
            assert await pool.call(lambda: 1) == 1
        finally:
            pool.close()

    asyncio.run(run())


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
