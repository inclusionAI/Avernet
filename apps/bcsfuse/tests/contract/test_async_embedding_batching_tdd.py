"""TDD for native-async batching that preserves the shared model budget."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


class _BatchBackend:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[list[str]] = []
        self.error = error
        self.closed = False

    @property
    def dimension(self) -> int:
        return 2

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        return [[float(index), float(len(texts))] for index, _ in enumerate(texts)]

    async def aclose(self) -> None:
        self.closed = True


class _CapacityStore:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.reserve_calls = 0

    def observe(self, model: str) -> tuple[str, float]:
        return ("", 0.0)

    def reserve(
        self,
        model: str,
        *,
        now_epoch_seconds: float,
        max_calls: int,
        period_seconds: float,
    ) -> tuple[bool, float, str]:
        self.reserve_calls += 1
        return (self.allowed, 0.0, "")


def test_concurrent_unique_queries_are_batched_by_the_native_async_provider() -> None:
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )

    backend = _BatchBackend()
    provider = AsyncBatchingEmbeddingProvider(
        backend,
        max_batch_size=3,
        batch_wait_seconds=0.01,
        max_workers=2,
    )

    async def scenario() -> list[list[float]]:
        return await asyncio.gather(
            *[provider.embed(f"query {index}") for index in range(6)]
        )

    vectors = asyncio.run(scenario())

    assert vectors == [
        [0.0, 3.0], [1.0, 3.0], [2.0, 3.0],
        [0.0, 3.0], [1.0, 3.0], [2.0, 3.0],
    ]
    assert len(backend.calls) == 2
    assert provider.stats["backend_batch_calls"] == 2
    assert provider.stats["batched_queries"] == 6
    assert provider.pending_queries == 0
    asyncio.run(provider.aclose())
    assert backend.closed is True


def test_batch_failure_is_propagated_to_every_pending_caller() -> None:
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )

    backend = _BatchBackend(error=RuntimeError("model unavailable"))
    provider = AsyncBatchingEmbeddingProvider(
        backend,
        max_batch_size=2,
        batch_wait_seconds=0.01,
        max_workers=1,
    )

    async def scenario() -> tuple[BaseException, BaseException]:
        first = asyncio.create_task(provider.embed("first"))
        second = asyncio.create_task(provider.embed("second"))
        try:
            await asyncio.gather(first, second)
        except BaseException as error:
            return error, error
        raise AssertionError("batch failure must propagate")

    error, observed = asyncio.run(scenario())

    assert isinstance(error, RuntimeError)
    assert error is observed
    assert provider.stats["backend_batch_calls"] == 1


def test_batching_provider_close_rejects_pending_calls_and_closes_backend() -> None:
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )

    backend = _BatchBackend()
    provider = AsyncBatchingEmbeddingProvider(
        backend,
        max_batch_size=4,
        batch_wait_seconds=30,
        max_workers=1,
    )

    pending: list[asyncio.Task[list[float]]] = []

    async def scenario() -> None:
        nonlocal pending
        pending = [asyncio.create_task(provider.embed(f"query {index}")) for index in range(2)]
        await asyncio.sleep(0)
        await provider.aclose()
        for task in pending:
            with pytest.raises(RuntimeError, match="embedding batching provider is closed"):
                await task

    asyncio.run(scenario())

    assert backend.calls == []
    assert backend.closed is True
    assert provider.closed is True


def test_async_circuit_batch_consumes_one_shared_capacity_slot() -> None:
    from app.infra.public.embedding.async_circuit_breaker_provider import (
        AsyncCircuitBreakerEmbeddingProvider,
    )

    backend = _BatchBackend()
    capacity = _CapacityStore()
    provider = AsyncCircuitBreakerEmbeddingProvider(
        backend,
        failure_threshold=2,
        recovery_timeout_seconds=10,
        capacity_store=capacity,
        max_calls_per_minute=30,
    )

    vectors = asyncio.run(provider.embed_batch(["one", "two", "three"]))

    assert vectors == [[0.0, 3.0], [1.0, 3.0], [2.0, 3.0]]
    assert capacity.reserve_calls == 1
    assert provider.state == "closed"


def test_batch_worker_slots_run_multiple_model_calls_concurrently() -> None:
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )

    class ConcurrencyBackend(_BatchBackend):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)
                return [
                    [float(index), float(len(texts))]
                    for index, _ in enumerate(texts)
                ]
            finally:
                self.active -= 1

    backend = ConcurrencyBackend()
    provider = AsyncBatchingEmbeddingProvider(
        backend,
        max_batch_size=16,
        batch_wait_seconds=0.01,
        max_workers=4,
    )

    async def scenario() -> list[list[float]]:
        return await asyncio.gather(
            *[provider.embed(f"query {index}") for index in range(64)]
        )

    results = asyncio.run(scenario())

    assert len(results) == 64
    assert len(backend.calls) == 4
    assert backend.max_active == 4


def test_http_embedding_batch_returns_one_result_per_input() -> None:
    from search_core.providers.embedding import AsyncHttpEmbedding

    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def post_json(
            self, url: str, *, headers: dict[str, str], json: dict[str, Any]
        ) -> dict[str, Any]:
            self.calls.append({"url": url, "payload": json})
            return {
                "data": [
                    {"index": index, "embedding": [float(index), 2.0]}
                    for index in range(len(json["input"]))
                ]
            }

    class _Settings:
        base_url = "http://127.0.0.1:1/v1"
        auth_token = "test-only"
        model = "batch-model"
        dimension = 2
        timeout_ms = 250

    provider = AsyncHttpEmbedding.__new__(AsyncHttpEmbedding)
    provider._settings = _Settings()
    provider._client = _Client()

    vectors = asyncio.run(provider.embed_batch(["one", "two"]))

    assert vectors == [[0.0, 2.0], [1.0, 2.0]]
    assert len(provider._client.calls) == 1
    assert provider._client.calls[0]["payload"]["input"] == ["one", "two"]
    assert provider._client.calls[0]["url"].endswith("/embeddings")


def test_runtime_composition_layers_batch_cache_circuit_and_http() -> None:
    from app.bootstrap.embedding_runtime_provider_composition import (
        register_embedding_runtime_providers,
    )
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )
    from search_core.providers.embedding import AsyncHttpEmbedding

    class Config:
        def get(self, key: str, default: Any = None) -> Any:
            if key == "embedding.auth_token":
                return "test-only"
            return default

        def get_int(self, key: str, default: int) -> int:
            return default

        def get_float(self, key: str, default: float) -> float:
            return default

    registry = ProviderRegistry()
    register_embedding_runtime_providers(
        registry,
        Config(),
        embedding_base_url="http://127.0.0.1:1/v1",
        embedding_model="batched-runtime",
        embedding_dimension=1024,
        embedding_request_dimensions=False,
    )
    provider = registry.get("agent_online_embedding_provider")
    assert isinstance(provider.backend, AsyncBatchingEmbeddingProvider)
    # The serving profile allows 32 upstream inputs and the deployment YAML
    # declares 32. A missing profile file must not silently halve cold capacity.
    assert provider.backend.max_batch_size == 32
    token_pool = provider.backend.backend
    assert isinstance(token_pool, AsyncEmbeddingTokenPoolProvider)
    assert isinstance(
        token_pool.lanes[0].backend, AsyncHttpEmbedding
    )


def test_native_async_batching_does_not_send_partial_batches_while_workers_are_active() -> None:
    """Prevent call amplification under a bounded HTTP admission semaphore."""
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )

    class BoundedConcurrencyBackend(_BatchBackend):
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            await asyncio.sleep(0.01)
            return [
                [float(index), float(len(texts))]
                for index, _ in enumerate(texts)
            ]

    backend = BoundedConcurrencyBackend()
    provider = AsyncBatchingEmbeddingProvider(
        backend,
        max_batch_size=32,
        batch_wait_seconds=0.01,
        max_workers=4,
    )
    semaphore = asyncio.Semaphore(100)

    async def call(index: int) -> list[float]:
        async with semaphore:
            return await provider.embed(f"query {index}")

    async def scenario() -> list[list[float]]:
        return await asyncio.gather(
            *[call(index) for index in range(512)]
        )

    results = asyncio.run(scenario())

    assert len(results) == 512
    assert provider.stats["backend_batch_calls"] == 16
    assert provider.stats["batched_queries"] == 512
    assert [len(call) for call in backend.calls] == [32] * 16



def test_partial_tail_waits_for_the_configured_arrival_window() -> None:
    """A tail batch must use batch_wait_seconds, not a fixed one-millisecond hold."""
    import asyncio

    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )

    release_initial_batch = asyncio.Event()

    class FullThenTailBackend(_BatchBackend):
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.calls.append(list(texts))
            await release_initial_batch.wait()
            return [
                [float(index), float(len(texts))] for index, _ in enumerate(texts)
            ]

    backend = FullThenTailBackend()
    provider = AsyncBatchingEmbeddingProvider(
        backend,
        max_batch_size=2,
        batch_wait_seconds=0.15,
        max_workers=1,
    )

    async def scenario() -> None:
        initial = [asyncio.create_task(provider.embed(f"full query {i}")) for i in range(2)]
        await asyncio.sleep(0.17)
        assert len(backend.calls) == 1
        tail = asyncio.create_task(provider.embed("tail query"))
        await asyncio.sleep(0)
        release_initial_batch.set()
        await asyncio.gather(*initial)

        await asyncio.sleep(0.05)
        assert len(backend.calls) == 1, (
            "partial tail must not be dispatched before batch_wait_seconds elapses"
        )

        await asyncio.sleep(0.18)
        assert len(backend.calls) == 2
        await tail

    asyncio.run(scenario())


def test_runtime_composition_binds_async_http_deadline_to_search_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The online HTTP deadline must not silently outlive the search stage."""

    from app.bootstrap.embedding_runtime_provider_composition import (
        register_embedding_runtime_providers,
    )
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )
    from search_core.providers.embedding import AsyncHttpEmbedding

    class Config:
        def get(self, key: str, default: Any = None) -> Any:
            if key == "embedding.auth_token":
                return "test-only"
            return default

        def get_int(self, key: str, default: int) -> int:
            return default

        def get_float(self, key: str, default: float) -> float:
            if key == "search.execution.embedding_timeout_seconds":
                return 1.25
            return default

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("EMBEDDING_MODEL", "bounded-http-runtime")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    monkeypatch.setenv("EMBEDDING_TIMEOUT_MS", "30000")

    registry = ProviderRegistry()
    register_embedding_runtime_providers(
        registry,
        Config(),
        embedding_base_url="http://127.0.0.1:1/v1",
        embedding_model="bounded-http-runtime",
        embedding_dimension=1024,
        embedding_request_dimensions=False,
    )
    provider = registry.get("agent_online_embedding_provider")

    assert isinstance(provider.backend, AsyncBatchingEmbeddingProvider)
    token_pool = provider.backend.backend
    assert isinstance(token_pool, AsyncEmbeddingTokenPoolProvider)
    lane = token_pool.lanes[0]
    assert isinstance(lane.backend, AsyncHttpEmbedding)
    assert lane.backend._settings.timeout_ms == 1250
