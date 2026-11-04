"""TDD for production-safe native async embedding providers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.infra.public.embedding.async_circuit_breaker_provider import (
    AsyncCircuitBreakerEmbeddingProvider,
    EmbeddingQuotaExhaustedError,
)
from app.infra.public.embedding.cached_async_embedding_provider import (
    CachedAsyncEmbeddingProvider,
)


class _FakeBackend:
    def __init__(
        self,
        *,
        vector: list[float] | None = None,
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.vector = vector or [0.5, 0.5]
        self.error = error
        self.delay = delay
        self.calls: list[str] = []
        self.closed = False

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return list(self.vector)

    async def aclose(self) -> None:
        self.closed = True


class _CapacityStore:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.reserve_calls = 0
        self.exhausted_calls = 0

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
        if self.allowed:
            return (True, 0.0, "")
        return (False, 1.0, "embedding_rate_limit_admission_rejected")

    def mark_exhausted(
        self, model: str, *, blocked_until_epoch_seconds: float, reason_code: str
    ) -> tuple[str, float]:
        self.exhausted_calls += 1
        return (reason_code, blocked_until_epoch_seconds)


def test_async_cache_miss_singleflight_and_hit() -> None:
    backend = _FakeBackend()
    provider = CachedAsyncEmbeddingProvider(backend, max_entries=10, ttl_seconds=30)

    async def scenario() -> tuple[list[list[float]], CachedAsyncEmbeddingProvider]:
        release = asyncio.Event()

        class WaitingBackend(_FakeBackend):
            async def embed(self, text: str) -> list[float]:
                self.calls.append(text)
                await release.wait()
                return list(self.vector)

        waiting = WaitingBackend(vector=[1.0])
        provider = CachedAsyncEmbeddingProvider(waiting, max_entries=10, ttl_seconds=30)
        first = asyncio.create_task(provider.embed("支付"))
        await asyncio.sleep(0)
        second = asyncio.create_task(provider.embed("  支付  "))
        await asyncio.sleep(0)
        release.set()
        return [await first, await second], provider

    vectors, provider = asyncio.run(scenario())

    assert vectors == [[1.0], [1.0]]
    assert provider.stats["cache_misses"] == 1
    assert provider.stats["cache_hits"] == 0


def test_async_cache_concurrent_callers_share_one_backend_request() -> None:
    class WaitingBackend(_FakeBackend):
        async def embed(self, text: str) -> list[float]:
            self.calls.append(text)
            await asyncio.sleep(0.05)
            return [0.25]

    backend = WaitingBackend()
    provider = CachedAsyncEmbeddingProvider(backend)

    async def scenario() -> list[list[float]]:
        return await asyncio.gather(
            provider.embed("reader"),
            provider.embed(" reader "),
            provider.embed(" reader "),

        )

    vectors = asyncio.run(scenario())

    assert vectors == [[0.25], [0.25], [0.25]]
    assert backend.calls == ["reader"]


def test_async_cache_does_not_cache_backend_failure() -> None:
    backend = _FakeBackend(error=RuntimeError("upstream failed"))
    provider = CachedAsyncEmbeddingProvider(backend, max_entries=10, ttl_seconds=30)

    with pytest.raises(RuntimeError):
        asyncio.run(provider.embed("reader"))
    backend.error = None
    vector = asyncio.run(provider.embed("reader"))

    assert vector == [0.5, 0.5]
    assert provider.stats["cache_misses"] == 2


def test_async_cache_is_bounded_and_closes_backend() -> None:
    backend = _FakeBackend()
    provider = CachedAsyncEmbeddingProvider(
        backend, max_entries=1, ttl_seconds=30, clock=lambda: 1.0
    )

    asyncio.run(provider.embed("one"))
    asyncio.run(provider.embed("two"))
    asyncio.run(provider.embed("one"))
    asyncio.run(provider.aclose())

    assert provider.pending_queries == 0
    assert provider.stats["cache_evictions"] == 2
    assert backend.closed is True


def test_async_circuit_opens_and_fail_fast_without_backend_calls() -> None:
    class QuotaError(RuntimeError):
        status_code = 429

    backend = _FakeBackend(error=QuotaError("quota"))
    provider = AsyncCircuitBreakerEmbeddingProvider(
        backend, failure_threshold=2, recovery_timeout_seconds=30
    )

    with pytest.raises(RuntimeError):
        asyncio.run(provider.embed("reader"))
    with pytest.raises(RuntimeError):
        asyncio.run(provider.embed("reader"))
    with pytest.raises(RuntimeError, match="circuit is open"):
        asyncio.run(provider.embed("reader"))

    assert backend.calls == ["reader", "reader"]
    assert provider.state == "open"


def test_async_circuit_success_closes_after_recovery_window() -> None:
    error = RuntimeError("upstream failed")
    now = {"value": 0.0}
    backend = _FakeBackend()
    provider = AsyncCircuitBreakerEmbeddingProvider(
        backend,
        failure_threshold=1,
        recovery_timeout_seconds=1.0,
        clock=lambda: now["value"],
    )

    class FailingBackend(_FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.raise_next = True

        async def embed(self, text: str) -> list[float]:
            self.calls.append(text)
            if self.raise_next:
                self.raise_next = False
                raise error
            return [1.0]

    failing = FailingBackend()
    provider = AsyncCircuitBreakerEmbeddingProvider(
        failing,
        failure_threshold=1,
        recovery_timeout_seconds=1.0,
        clock=lambda: now["value"],
    )
    with pytest.raises(RuntimeError):
        asyncio.run(provider.embed("reader"))
    now["value"] = 2.0
    vector = asyncio.run(provider.embed("reader"))
    second = asyncio.run(provider.embed("reader"))

    assert vector == [1.0]
    assert second == [1.0]
    assert provider.state == "closed"
    assert failing.calls == ["reader", "reader", "reader"]


def test_async_circuit_converts_429_to_shared_quota_contract() -> None:
    class QuotaError(RuntimeError):
        status_code = 429
        provider_reason_code = "RPM_LIMIT_EXCEEDED"

    backend = _FakeBackend(error=QuotaError("model quota"))
    capacity = _CapacityStore(allowed=False)
    provider = AsyncCircuitBreakerEmbeddingProvider(
        backend,
        failure_threshold=1,
        recovery_timeout_seconds=30,
        capacity_store=capacity,
        max_calls_per_minute=10,
        quota_recovery_seconds=42.0,
    )
    provider.max_calls_per_minute = 0
    backend.vector = []

    with pytest.raises(EmbeddingQuotaExhaustedError) as caught:
        asyncio.run(provider.embed("reader"))

    assert caught.value.status_code == 429
    assert 41.0 < caught.value.retry_after_seconds <= 42.0
    assert caught.value.reason_code == "embedding_quota_exhausted"
    assert caught.value.provider_reason_code == "RPM_LIMIT_EXCEEDED"
    assert capacity.exhausted_calls == 1
    assert provider.state == "open"


def test_async_circuit_shared_admission_rejection_is_fail_fast() -> None:
    backend = _FakeBackend()
    capacity = _CapacityStore(allowed=False)
    provider = AsyncCircuitBreakerEmbeddingProvider(
        backend,
        capacity_store=capacity,
        max_calls_per_minute=10,
    )

    with pytest.raises(EmbeddingQuotaExhaustedError) as caught:
        asyncio.run(provider.embed("reader"))

    assert caught.value.reason_code == "embedding_rate_limit_admission_rejected"
    assert backend.calls == []


def test_async_capacity_store_errors_fail_open() -> None:
    class FailingStore:
        def observe(self, model: str) -> tuple[str, float]:
            raise RuntimeError("store unavailable")

        def reserve(self, *args: Any, **kwargs: Any) -> tuple[bool, float, str]:
            raise RuntimeError("store unavailable")

    backend = _FakeBackend()
    provider = AsyncCircuitBreakerEmbeddingProvider(
        backend,
        capacity_store=FailingStore(),
        max_calls_per_minute=10,
    )

    vector = asyncio.run(provider.embed("reader"))

    assert vector == [0.5, 0.5]


def test_async_circuit_closes_backend() -> None:
    backend = _FakeBackend()
    provider = AsyncCircuitBreakerEmbeddingProvider(backend)

    asyncio.run(provider.aclose())

    assert backend.closed is True


def _fake_config() -> Any:
    class Config:
        def get(self, key: str, default: Any = None) -> Any:
            return default

        def get_int(self, key: str, default: int = 0) -> int:
            return default

        def get_float(self, key: str, default: float = 0.0) -> float:
            return default

    return Config()


def test_runtime_composition_builds_an_online_native_async_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from search_core.providers.embedding import AsyncHttpEmbedding

    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )
    from app.infra.public.embedding.embedding_token_pool_provider import (
        EmbeddingTokenPoolProvider,
    )
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("EMBEDDING_MODEL", "native-async-composition")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    monkeypatch.setenv("EMBEDDING_REQUEST_DIMENSIONS", "false")
    monkeypatch.setenv("EMBEDDING_TIMEOUT_MS", "2500")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    registry = ProviderRegistry()
    register_runtime_service_providers(registry, _fake_config())
    provider = registry.get("agent_online_embedding_provider")
    sync_provider = registry.get("embedding_provider")

    assert isinstance(provider, CachedAsyncEmbeddingProvider)
    assert isinstance(
        provider.backend, AsyncBatchingEmbeddingProvider
    )
    assert isinstance(
        provider.backend.backend, AsyncEmbeddingTokenPoolProvider
    )
    token_pool = provider.backend.backend
    assert token_pool.lane_count == 1
    assert isinstance(
        token_pool.lanes[0], AsyncCircuitBreakerEmbeddingProvider
    )
    assert isinstance(token_pool.lanes[0].backend, AsyncHttpEmbedding)
    assert provider.dimension == 1024
    assert token_pool.lanes[0].failure_threshold == 3
    assert provider.cache_max_entries == 10000
    assert provider.cache_ttl_seconds == 300.0
    assert isinstance(sync_provider.backend, EmbeddingTokenPoolProvider)
    assert isinstance(
        sync_provider.backend.lanes[0],
        CircuitBreakerEmbeddingProvider,
    )
    assert asyncio.iscoroutinefunction(provider.embed)


def test_online_provider_selection_falls_back_to_sync_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.application.services.agent_search_provider_selection import (
        select_online_embedding_provider,
    )
    from app.bootstrap.provider_registry import ProviderRegistry

    class SyncProvider:
        def embed(self, text: str) -> list[float]:
            return [1.0]

    registry = ProviderRegistry()
    fallback = SyncProvider()

    assert select_online_embedding_provider(registry, fallback) is fallback

    registry.register("agent_online_embedding_provider", CachedAsyncEmbeddingProvider(_FakeBackend()))

    assert select_online_embedding_provider(registry, fallback) is not fallback


def test_runtime_composition_falls_back_without_real_embedding_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "EMBEDDING_BASE_URL",
        "EMBEDDING_AUTH_TOKEN",
        "EMBEDDING_MODEL",
        "LLM_BASE_URL",
        "LLM_AUTH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry

    registry = ProviderRegistry()
    register_runtime_service_providers(registry, _fake_config())

    assert registry.get("agent_online_embedding_provider") is registry.get(
        "embedding_provider"
    )


def test_runtime_composition_closes_native_async_client_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.providers.public.noop_startup_provider import NoopStartupProvider

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("EMBEDDING_MODEL", "native-shutdown")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    monkeypatch.setenv("EMBEDDING_REQUEST_DIMENSIONS", "false")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    registry = ProviderRegistry()
    registry.register("startup_provider", NoopStartupProvider())
    from app.bootstrap.opensource import register_runtime_service_providers

    register_runtime_service_providers(registry, _fake_config())
    lifecycle = registry.get("startup_provider")
    assert lifecycle is not None
    asyncio.run(lifecycle.shutdown())

    provider = registry.get("agent_online_embedding_provider")
    assert isinstance(provider, CachedAsyncEmbeddingProvider)
    assert provider.closed is True


def test_async_online_provider_negotiates_dimensions_inside_the_token_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("EMBEDDING_MODEL", "Qwen3-Embedding-8B")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    monkeypatch.setenv("EMBEDDING_REQUEST_DIMENSIONS", "true")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    registry = ProviderRegistry()
    register_runtime_service_providers(registry, _fake_config())

    online = registry.get("agent_online_embedding_provider")
    pool = online.backend.backend
    assert isinstance(pool, AsyncEmbeddingTokenPoolProvider)
    assert bool(pool.lanes[0].backend._settings.request_dimensions)
    assert pool.dimension == 1024
def test_async_cache_close_wakes_pending_followers_instead_of_leaking_waits() -> None:
    class BlockingUntilClosedBackend(_FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()
            self.closed = False

        async def embed(self, text: str) -> list[float]:
            self.calls.append(text)
            await self.release.wait()
            return list(self.vector)

        async def aclose(self) -> None:
            self.release.set()
            self.closed = True

    backend = BlockingUntilClosedBackend()
    provider = CachedAsyncEmbeddingProvider(backend, max_entries=10, ttl_seconds=30)

    async def scenario() -> BaseException:
        leader = asyncio.create_task(provider.embed("reader"))
        await asyncio.sleep(0)
        follower = asyncio.create_task(provider.embed("reader"))
        await asyncio.sleep(0)
        assert provider.pending_queries == 1
        await provider.aclose()
        try:
            await asyncio.wait_for(follower, timeout=0.2)
        except BaseException as error:
            await leader
            return error
        await leader
        raise AssertionError("pending cache follower must wake on shutdown")

    error = asyncio.run(scenario())

    assert isinstance(error, RuntimeError)
    assert "closed" in str(error)
    assert backend.closed is True
    assert provider.pending_queries == 0
    assert provider.closed is True


def test_async_cache_rejects_new_queries_after_shutdown() -> None:
    backend = _FakeBackend()
    provider = CachedAsyncEmbeddingProvider(backend, max_entries=10, ttl_seconds=30)

    asyncio.run(provider.embed("first"))
    asyncio.run(provider.aclose())
    backend.calls.clear()

    with pytest.raises(RuntimeError, match="embedding cache provider is closed"):
        asyncio.run(provider.embed("second"))

    assert backend.calls == []
