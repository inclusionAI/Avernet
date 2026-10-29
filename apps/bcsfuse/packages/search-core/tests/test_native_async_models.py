"""Async model I/O must not occupy or queue behind synchronous index workers."""

import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor

import pytest
from search_core.api.models import RetrievalRequest
from search_core.config.embedding import EmbeddingSettings
from search_core.core.execution import DeadlineExecutor
from search_core.core.retrieval import HybridRetrievalService
from search_core.plugin_api.vector_search_hit import VectorSearchHit
from search_core.providers.async_model_http import AsyncModelHttpClient, ModelHTTPError


def test_deadline_executor_awaits_native_async_call_without_submitting_threads():
    class NoThreads:
        def submit(self, *args):
            pytest.fail("native async model must not use the index thread pool")

    async def run():
        async def model():
            await asyncio.sleep(0)
            return "model-result"

        result = await DeadlineExecutor(
            NoThreads(), timeout_seconds=1, name="embedding"
        ).call(model)
        try:
            assert result == "model-result"
        finally:
            if inspect.iscoroutine(result):
                result.close()

    asyncio.run(run())


@pytest.mark.parametrize("blocked", ["embedding", "reranker"])
def test_64_native_async_model_waits_preserve_keyword_fallback(blocked):
    class Index:
        def keyword_search(self, *args):
            return [VectorSearchHit(id="a", score=1, payload={"name": "A"})]

        vector_search = keyword_search

    async def run(executor):
        active = 0

        async def hang():
            nonlocal active
            active += 1
            try:
                await asyncio.sleep(10)
            finally:
                active -= 1

        class Embedding:
            async def embed(self, text):
                if blocked == "embedding":
                    await hang()
                return [1.0, 0.0]

        class Reranker:
            async def rerank(self, *args):
                await hang()

        calls = {
            name: DeadlineExecutor(executor, name=name, timeout_seconds=timeout)
            for name, timeout in (("embedding", 0.2), ("index", 0.5), ("reranker", 0.2))
        }
        service = HybridRetrievalService(
            index=Index(),
            embedding=Embedding(),
            reranker=Reranker() if blocked == "reranker" else None,
            allow_partial=True,
            execution=calls,
        )
        results = await asyncio.gather(
            *(service.retrieve(RetrievalRequest("x", 1, {}, None)) for _ in range(64))
        )
        assert all(
            r.status == "degraded" and r.items[0].item_id == "a" for r in results
        )
        assert all(r.reasons == (blocked + "_unavailable",) for r in results)
        assert active == 0

    # One index worker makes any accidental model-thread submission visible.
    with ThreadPoolExecutor(max_workers=1) as executor:
        asyncio.run(run(executor))


def test_native_embedding_request_validation_and_no_retry(monkeypatch):
    from search_core.providers.embedding import AsyncHttpEmbedding

    async def run():
        statuses = [200, 429, 200]
        seen = []

        async def respond(self, url, *, headers, json):
            seen.append(json)
            status = statuses[len(seen) - 1]
            if status >= 400:
                raise ModelHTTPError(status)
            return {"data": [{"embedding": [1.0] * 64}]}

        monkeypatch.setattr(AsyncModelHttpClient, "post_json", respond)
        provider = AsyncHttpEmbedding(
            EmbeddingSettings(
                base_url="https://models.example.test/v1",
                auth_token="test-only",
                model="example",
                dimension=64,
            )
        )
        try:
            assert await provider.embed("hello") == [1.0] * 64
            with pytest.raises(ModelHTTPError) as error:
                await provider.embed("quota")
            assert error.value.status_code == 429
            assert await provider.embed("recovered") == [1.0] * 64
            assert [r["input"] for r in seen] == [["hello"], ["quota"], ["recovered"]]
        finally:
            await provider.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_cancel_native_http_closes_sockets_without_100_connection_ceiling(kind):
    from search_core.providers.embedding import AsyncHttpEmbedding
    from search_core.providers.reranker import AsyncHttpReranker

    async def run():
        entered = closed = 0
        all_entered, all_closed = asyncio.Event(), asyncio.Event()
        handlers = set()

        async def stalled(reader, writer):
            nonlocal entered, closed
            task = asyncio.current_task()
            handlers.add(task)
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                await reader.readexactly(length)
                entered += 1
                if entered == 128:
                    all_entered.set()
                # Never send a response: a cancelled client must close its socket.
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                closed += 1
                if closed == 128:
                    all_closed.set()
                handlers.discard(task)

        server = await asyncio.start_server(stalled, "127.0.0.1", 0)
        url = "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
        if kind == "embedding":
            provider = AsyncHttpEmbedding(
                EmbeddingSettings(
                    base_url=url,
                    auth_token="test-only",
                    model="test",
                    dimension=64,
                    timeout_ms=10000,
                )
            )
            calls = [asyncio.create_task(provider.embed("x")) for _ in range(128)]
        else:
            provider = AsyncHttpReranker(base_url=url, api_key="test-only", timeout=10)
            calls = [
                asyncio.create_task(provider.rerank("x", [{"id": "a", "text": "x"}]))
                for _ in range(128)
            ]
        try:
            await asyncio.wait_for(all_entered.wait(), 5)
            for task in calls:
                task.cancel()
            results = await asyncio.gather(*calls, return_exceptions=True)
            assert all(isinstance(result, asyncio.CancelledError) for result in results)
            await asyncio.wait_for(all_closed.wait(), 5)
        finally:
            for task in calls:
                task.cancel()
            await asyncio.gather(*calls, return_exceptions=True)
            await provider.aclose()
            server.close()
            await server.wait_closed()
            for task in tuple(handlers):
                task.cancel()
            await asyncio.gather(*tuple(handlers), return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("model", ["bge-reranker-v2-m3", "Qwen3-Reranker-4B"])
def test_native_reranker_retains_model_format_and_scores(monkeypatch, model):
    from search_core.providers.reranker import AsyncHttpReranker

    async def run():
        sent = []

        async def respond(self, url, *, headers, json):
            sent.append(json)
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ]
            }

        monkeypatch.setattr(AsyncModelHttpClient, "post_json", respond)
        provider = AsyncHttpReranker(
            base_url="https://models.example.test/v1",
            api_key="test-only",
            model=model,
            timeout=1,
        )
        try:
            result = await provider.rerank(
                "question",
                [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}],
                2,
            )
            assert [(r.candidate_id, r.score) for r in result] == [
                ("b", 0.9),
                ("a", 0.1),
            ]
            assert sent[0]["model"] == model
            if model.startswith("bge"):
                assert sent[0]["query"] == "question"
                assert sent[0]["documents"] == ["first", "second"]
            else:
                assert "<Query>: question" in sent[0]["query"]
                assert sent[0]["documents"][0].startswith("first")
                assert "<|im_end|>" in sent[0]["documents"][0]
        finally:
            await provider.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_malformed_model_output_is_not_reported_as_a_valid_result(monkeypatch, kind):
    from search_core.providers.embedding import AsyncHttpEmbedding
    from search_core.providers.reranker import AsyncHttpReranker

    async def run():
        async def respond(self, url, *, headers, json):
            return {"data": [{"embedding": [1.0]}], "scores": []}

        monkeypatch.setattr(AsyncModelHttpClient, "post_json", respond)
        if kind == "embedding":
            provider = AsyncHttpEmbedding(
                EmbeddingSettings(
                    base_url="https://models.example.test/v1",
                    auth_token="test-only",
                    model="test",
                    dimension=64,
                )
            )
            call = provider.embed("x")
        else:
            provider = AsyncHttpReranker(
                base_url="https://models.example.test/v1", api_key="test-only"
            )
            call = provider.rerank("x", [{"id": "a", "text": "x"}])
        try:
            with pytest.raises(ValueError):
                await call
        finally:
            await provider.aclose()

    asyncio.run(run())
