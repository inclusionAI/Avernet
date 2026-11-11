"""Coalesce overlapping embeddings without delaying or outliving their callers."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from search_core.application.cache import CachedEmbedding
from search_core.core.execution import DeadlineExecutor
from search_core.core.memory_cache import MemoryCache


class Model:
    dimension = 2

    def __init__(self, *, error=None, vector=(1.0, 0.0)):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = 0
        self.error, self.vector = error, vector

    async def embed(self, text):
        self.calls.append(text)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.error is not None:
            raise self.error
        return list(self.vector)


def cached(model, **options):
    return CachedEmbedding(
        model,
        MemoryCache(enabled=True, ttl_ms=10000, max_entries=16, max_bytes=4096),
        "model/2",
        **options,
    )


def test_overlapping_identical_queries_call_model_once_and_copy_results():
    async def run():
        model = Model()
        provider = cached(model)
        tasks = [asyncio.create_task(provider.embed("same")) for _ in range(16)]
        await model.started.wait()  # The first call starts without a batch window.
        model.release.set()
        results = await asyncio.gather(*tasks)
        assert model.calls == ["same"]
        assert results == [[1.0, 0.0]] * 16
        results[0][0] = 99
        assert results[1] == [1.0, 0.0]
        assert await provider.embed("same") == [1.0, 0.0]
        assert model.calls == ["same"]

    asyncio.run(asyncio.wait_for(run(), 2))


def test_disabled_singleflight_retains_cache_without_sharing_pending_calls():
    async def run():
        model = Model()
        provider = cached(model, singleflight_enabled=False)
        tasks = [asyncio.create_task(provider.embed("same")) for _ in range(3)]
        await model.started.wait()
        model.release.set()
        assert await asyncio.gather(*tasks) == [[1.0, 0.0]] * 3
        assert model.calls == ["same"] * 3
        assert await provider.embed("same") == [1.0, 0.0]
        assert model.calls == ["same"] * 3

    asyncio.run(asyncio.wait_for(run(), 2))


def test_different_queries_start_independently():
    async def run():
        model = Model()
        provider = cached(model)
        first = asyncio.create_task(provider.embed("first"))
        second = asyncio.create_task(provider.embed("second"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert model.calls == ["first", "second"]
        model.release.set()
        assert await asyncio.gather(first, second) == [[1.0, 0.0], [1.0, 0.0]]

    asyncio.run(asyncio.wait_for(run(), 2))


def test_synchronous_provider_still_runs_once_for_overlapping_queries():
    class SyncModel:
        dimension = 2
        calls = 0

        def embed(self, text):
            self.calls += 1
            return [1.0, 0.0]

    async def run():
        model = SyncModel()
        provider = cached(model)
        results = await asyncio.gather(*(provider.embed("same") for _ in range(16)))
        assert results == [[1.0, 0.0]] * 16
        assert model.calls == 1

    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize("cancel_first", [True, False])
def test_cancelling_one_waiter_does_not_cancel_its_peer(cancel_first):
    async def run():
        model = Model()
        provider = cached(model)
        first = asyncio.create_task(provider.embed("same"))
        await model.started.wait()
        second = asyncio.create_task(provider.embed("same"))
        await asyncio.sleep(0)
        cancelled, survivor = (first, second) if cancel_first else (second, first)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert model.cancelled == 0
        model.release.set()
        assert await survivor == [1.0, 0.0]
        assert model.calls == ["same"]

    asyncio.run(asyncio.wait_for(run(), 2))


def test_last_waiter_timeout_cancels_model_and_allows_fresh_retry():
    async def run():
        model = Model()
        provider = cached(model)
        with ThreadPoolExecutor(max_workers=1) as executor:
            call = DeadlineExecutor(executor, timeout_seconds=0.02, name="embedding")
            with pytest.raises(TimeoutError):
                await call.call(provider.embed, "same")
        assert model.calls == ["same"]
        assert model.cancelled == 1
        model.release.set()
        assert await provider.embed("same") == [1.0, 0.0]
        assert model.calls == ["same", "same"]

    asyncio.run(asyncio.wait_for(run(), 2))


def test_first_request_deadline_does_not_shorten_later_request_deadline():
    async def run():
        model = Model()
        provider = cached(model)
        with ThreadPoolExecutor(max_workers=1) as executor:
            short = DeadlineExecutor(executor, timeout_seconds=0.02, name="embedding")
            long = DeadlineExecutor(executor, timeout_seconds=1, name="embedding")
            first = asyncio.create_task(short.call(provider.embed, "same"))
            await model.started.wait()
            second = asyncio.create_task(long.call(provider.embed, "same"))
            with pytest.raises(TimeoutError):
                await first
            assert model.cancelled == 0
            model.release.set()
            assert await second == [1.0, 0.0]
            assert model.calls == ["same"]

    asyncio.run(asyncio.wait_for(run(), 2))


@pytest.mark.parametrize("invalid", [False, True])
def test_shared_failure_is_not_cached_and_next_request_retries(invalid):
    async def run():
        model = (
            Model(vector=(float("nan"), 0.0))
            if invalid
            else Model(error=RuntimeError("model unavailable"))
        )
        provider = cached(model)
        tasks = [asyncio.create_task(provider.embed("same")) for _ in range(3)]
        await model.started.wait()
        model.release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        expected = ValueError if invalid else RuntimeError
        assert all(isinstance(result, expected) for result in results)
        assert model.calls == ["same"]
        model.error, model.vector = None, (1.0, 0.0)
        assert await provider.embed("same") == [1.0, 0.0]
        assert model.calls == ["same", "same"]

    asyncio.run(asyncio.wait_for(run(), 2))
