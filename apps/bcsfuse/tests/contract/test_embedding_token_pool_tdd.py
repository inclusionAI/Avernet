"""TDD: every pod consumes one shared, stateless embedding token pool.

Tokens enter only through composition configuration and are never placed in
source code, metrics, logs, durable capacity keys, or response data.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.infra.public.embedding.async_circuit_breaker_provider import (
    EmbeddingQuotaExhaustedError,
)


class FakeAsyncLane:
    def __init__(
        self,
        name: str,
        vector: list[float] | None = None,
        quota_error: BaseException | None = None,
    ) -> None:
        self.name = name
        self.vector = vector or [float(len(name))]
        self.quota_error = quota_error
        self.calls: list[str] = []
        self.closed = False
        self.model = "token-pool-model"
        self.dimension = 3

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.extend(texts)
        if self.quota_error is not None:
            raise self.quota_error
        return [list(self.vector) for _ in texts]

    async def aclose(self) -> None:
        self.closed = True


class FakeSyncLane:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls: list[str] = []
        self.closed = False
        self.model = "sync-token-pool-model"
        self.dimension = 2

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return list(self.vector)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.extend(texts)
        return [list(self.vector) for _ in texts]

    def close(self) -> None:
        self.closed = True


class RecordingCapacityStore:
    def __init__(self) -> None:
        self.observer_keys: list[str] = []

    def observe(self, key: str) -> tuple[str, float]:
        self.observer_keys.append(key)
        return ("available", 0.0)

    def reserve(self, *_args: Any, **_kwargs: Any) -> tuple[bool, float, str]:
        return (True, 0.0, "available")

    def mark_exhausted(
        self,
        key: str,
        *,
        blocked_until_epoch_seconds: float,
        reason_code: str,
    ) -> tuple[str, float]:
        return (reason_code, blocked_until_epoch_seconds)


class RuntimeConfig:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def get_int(self, key: str, default: int) -> int:
        value = self.values.get(key, default)
        return int(value)

    def get_float(self, key: str, default: float) -> float:
        value = self.values.get(key, default)
        return float(value)


def _quota(retry_after: float = 30.0) -> EmbeddingQuotaExhaustedError:
    return EmbeddingQuotaExhaustedError(
        "token lane exhausted",
        retry_after_seconds=retry_after,
        reason_code="embedding_quota_exhausted",
    )


def test_async_pool_rejects_an_empty_lane_list() -> None:
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    with pytest.raises(ValueError, match="at least one token"):
        AsyncEmbeddingTokenPoolProvider([])


def test_async_pool_rotates_the_same_shared_lanes_for_every_pod() -> None:
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    first = FakeAsyncLane("first")
    second = FakeAsyncLane("second")
    provider = AsyncEmbeddingTokenPoolProvider([first, second])

    async def scenario() -> list[list[float]]:
        return await asyncio.gather(*[
            provider.embed(f"query-{index}") for index in range(4)
        ])

    vectors = asyncio.run(scenario())

    assert len(vectors) == 4
    assert first.calls == ["query-0", "query-2"]
    assert second.calls == ["query-1", "query-3"]
    assert provider.dimension == 3


def test_async_pool_preserves_one_batch_on_one_token_lane() -> None:
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    first = FakeAsyncLane("first")
    second = FakeAsyncLane("second")
    provider = AsyncEmbeddingTokenPoolProvider([first, second])

    vectors = asyncio.run(provider.embed_batch(["one", "two", "three"]))

    assert len(first.calls) + len(second.calls) == 3
    assert (first.calls == ["one", "two", "three"]) != (second.calls == ["one", "two", "three"])
    assert len(vectors) == 3


def test_async_pool_fails_over_to_another_token_after_429() -> None:
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    exhausted = FakeAsyncLane("exhausted", quota_error=_quota())
    healthy = FakeAsyncLane("healthy")
    provider = AsyncEmbeddingTokenPoolProvider([exhausted, healthy])

    vector = asyncio.run(provider.embed("reader"))

    assert vector == healthy.vector
    assert exhausted.calls == ["reader"]
    assert healthy.calls == ["reader"]


def test_async_pool_fails_over_to_the_final_token_and_aggregates_all_lane_429s() -> None:
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    first = FakeAsyncLane("first", quota_error=_quota(30.0))
    second = FakeAsyncLane("second", quota_error=_quota(10.0))
    provider = AsyncEmbeddingTokenPoolProvider([first, second])

    with pytest.raises(EmbeddingQuotaExhaustedError) as caught:
        asyncio.run(provider.embed("reader"))

    assert caught.value.reason_code == "embedding_quota_exhausted"
    assert caught.value.retry_after_seconds == pytest.approx(10.0)
    assert first.calls == ["reader"]
    assert second.calls == ["reader"]


def test_async_pool_closes_every_shared_lane_without_binding_a_token_to_a_pod() -> None:
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    first = FakeAsyncLane("first")
    second = FakeAsyncLane("second")
    provider = AsyncEmbeddingTokenPoolProvider([first, second])

    asyncio.run(provider.aclose())

    assert first.closed is True
    assert second.closed is True


def test_sync_pool_rotates_and_fails_over_429_lanes() -> None:
    from app.infra.public.embedding.embedding_token_pool_provider import (
        EmbeddingTokenPoolProvider,
    )

    class ExhaustedLane(FakeSyncLane):
        def embed(self, text: str) -> list[float]:
            self.calls.append(text)
            raise _quota()

    exhausted = ExhaustedLane([1.0, 0.0])
    healthy = FakeSyncLane([0.0, 1.0])
    provider = EmbeddingTokenPoolProvider([exhausted, healthy])

    vector = provider.embed("reader")

    assert vector == [0.0, 1.0]
    assert exhausted.calls == ["reader"]
    assert healthy.calls == ["reader"]
    assert provider.dimension == 2

    provider.close()
    assert exhausted.closed is True
    assert healthy.closed is True
def test_async_circuit_capacity_key_is_token_scoped_not_model_scoped() -> None:
    from app.infra.public.embedding.async_circuit_breaker_provider import (
        AsyncCircuitBreakerEmbeddingProvider,
    )

    backend = FakeAsyncLane("backend")
    capacity = RecordingCapacityStore()
    provider = AsyncCircuitBreakerEmbeddingProvider(
        backend,
        capacity_store=capacity,
        capacity_key="model:token-lane-1",
    )

    asyncio.run(provider.embed("reader"))

    assert provider.capacity_key == "model:token-lane-1"
    assert provider.metric_key == "model:token-lane-1"
    assert provider.model == backend.model
    assert capacity.observer_keys == ["model:token-lane-1"]


def test_token_pool_resolver_accepts_json_and_comma_sources() -> None:
    from app.bootstrap.embedding_token_pool_settings import (
        resolve_embedding_token_pool,
    )

    json_pool = json.dumps(["token-a", "token-b"])
    assert resolve_embedding_token_pool(
        RuntimeConfig(),
        {"EMBEDDING_AUTH_TOKENS": json_pool},
    ) == ["token-a", "token-b"]
    assert resolve_embedding_token_pool(
        RuntimeConfig(),
        {"EMBEDDING_AUTH_TOKENS": " token-c , ,token-d "},
    ) == ["token-c", "token-d"]
    assert resolve_embedding_token_pool(
        RuntimeConfig(),
        {"EMBEDDING_AUTH_TOKENS": json.dumps([" token-e ", "token-a"])},
    ) == ["token-e", "token-a"]
def test_token_pool_resolver_keeps_single_token_compatibility_and_rejects_blank_pool() -> None:
    from app.bootstrap.embedding_token_pool_settings import (
        resolve_embedding_token_pool,
    )

    assert resolve_embedding_token_pool(
        RuntimeConfig(),
        {"EMBEDDING_AUTH_TOKEN": " test-token "},
    ) == ["test-token"]
    assert resolve_embedding_token_pool(
        RuntimeConfig(),
        {"EMBEDDING_AUTH_TOKENS": ""},
    ) == []
    # An explicitly empty pool must not fall back to a stale single token.
    assert resolve_embedding_token_pool(
        RuntimeConfig(),
        {
            "EMBEDDING_AUTH_TOKEN": " test-token ",
            "EMBEDDING_AUTH_TOKENS": "",
        },
    ) == []

    with pytest.raises(ValueError, match="non-empty"):
        resolve_embedding_token_pool(RuntimeConfig(), {})
        resolve_embedding_token_pool(RuntimeConfig(), {}, require_at_least_one=True)


def test_runtime_composition_builds_shared_pools_for_sync_and_async_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bootstrap.embedding_token_pool_settings import token_lane_key
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
    from app.infra.public.embedding.cached_async_embedding_provider import (
        CachedAsyncEmbeddingProvider,
    )
    from app.infra.public.embedding.cached_embedding_provider import (
        CachedEmbeddingProvider,
    )
    from app.infra.public.embedding.embedding_token_pool_provider import (
        EmbeddingTokenPoolProvider,
    )
    from search_core.providers.embedding import AsyncHttpEmbedding

    for name in ("EMBEDDING_AUTH_TOKEN", "EMBEDDING_AUTH_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMBEDDING_AUTH_TOKENS", "token-a,token-b")

    registry = ProviderRegistry()
    store = RecordingCapacityStore()
    registry.register("embedding_capacity_store", store)
    register_embedding_runtime_providers(
        registry,
        RuntimeConfig(),
        embedding_base_url="http://127.0.0.1:1/v1",
        embedding_model="shared-token-model",
        embedding_dimension=1024,
        embedding_request_dimensions=True,
    )

    sync_cache = registry.get("embedding_provider")
    online_cache = registry.get("agent_online_embedding_provider")
    assert isinstance(sync_cache, CachedEmbeddingProvider)
    assert isinstance(sync_cache.backend, EmbeddingTokenPoolProvider)
    assert len(sync_cache.backend.lanes) == 2
    assert {
        lane.capacity_key for lane in sync_cache.backend.lanes
    } == {
        f"shared-token-model:{token_lane_key('token-a')}",
        f"shared-token-model:{token_lane_key('token-b')}",
    }
    assert all(
        lane.metric_key == lane.capacity_key
        for lane in sync_cache.backend.lanes
    )

    assert isinstance(online_cache, CachedAsyncEmbeddingProvider)
    assert isinstance(online_cache.backend, AsyncBatchingEmbeddingProvider)
    assert isinstance(online_cache.backend.backend, AsyncEmbeddingTokenPoolProvider)
    async_pool = online_cache.backend.backend
    assert len(async_pool.lanes) == 2
    for lane in async_pool.lanes:
        assert isinstance(lane.backend, AsyncHttpEmbedding)
        assert isinstance(lane.capacity_store, RecordingCapacityStore)
        assert lane.metric_key == lane.capacity_key
        assert "token-a" not in lane.capacity_key
        assert "token-b" not in lane.capacity_key
        assert "token-a" not in lane.metric_key
        assert "token-b" not in lane.metric_key
    assert online_cache is not sync_cache


def test_runtime_composition_accepts_request_dimensions_on_native_async_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bootstrap.embedding_runtime_provider_composition import (
        register_embedding_runtime_providers,
    )
    from app.bootstrap.provider_registry import ProviderRegistry

    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    registry = ProviderRegistry()
    register_embedding_runtime_providers(
        registry,
        RuntimeConfig(),
        embedding_base_url="http://127.0.0.1:1/v1",
        embedding_model="shared-token-model",
        embedding_dimension=1024,
        embedding_request_dimensions=True,
    )

    provider = registry.get("agent_online_embedding_provider")
    assert asyncio.iscoroutinefunction(provider.embed)


def test_async_pool_failover_is_bounded_for_a_large_shared_pool() -> None:
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    lanes = [
        FakeAsyncLane(f"lane-{index}", quota_error=_quota())
        for index in range(5)
    ]
    provider = AsyncEmbeddingTokenPoolProvider(
        lanes,
        max_failover_attempts=2,
    )

    with pytest.raises(EmbeddingQuotaExhaustedError):
        asyncio.run(provider.embed("reader"))

    assert sum(len(lane.calls) for lane in lanes) == 2
    assert provider.lane_count == 5


def test_sync_pool_failover_is_bounded_for_a_large_shared_pool() -> None:
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingQuotaExhaustedError as SyncEmbeddingQuotaExhaustedError,
    )
    from app.infra.public.embedding.embedding_token_pool_provider import (
        EmbeddingTokenPoolProvider,
    )

    class ExhaustedLane(FakeSyncLane):
        def embed(self, text: str) -> list[float]:
            self.calls.append(text)
            raise _quota()

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.calls.extend(texts)
            raise _quota()

    lanes = [ExhaustedLane([float(index)]) for index in range(5)]
    provider = EmbeddingTokenPoolProvider(
        lanes,
        max_failover_attempts=2,
    )

    with pytest.raises(SyncEmbeddingQuotaExhaustedError):
        provider.embed("reader")

    assert sum(len(lane.calls) for lane in lanes) == 2
    assert provider.lane_count == 5


def test_yaml_config_maps_the_shared_token_pool_and_masks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bootstrap.oss_diagnostics import mask_secret_value
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    monkeypatch.setenv("EMBEDDING_AUTH_TOKENS", "token-a,token-b")
    config = YamlEnvConfigProvider(config_path="/tmp/nonexistent-bcsfuse.yaml")

    assert config.get("embedding.auth_tokens") == "token-a,token-b"
    masked = mask_secret_value("EMBEDDING_AUTH_TOKENS", "token-a")
    assert isinstance(masked, str)
    assert "token-a" not in masked


def test_runtime_token_pool_failover_budget_is_composition_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.bootstrap.embedding_runtime_provider_composition import (
        register_embedding_runtime_providers,
    )
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    monkeypatch.setenv("EMBEDDING_AUTH_TOKENS", "token-a,token-b,token-c")
    registry = ProviderRegistry()
    register_embedding_runtime_providers(
        registry,
        RuntimeConfig(
            {"embedding.token_pool.max_failover_attempts": 2}
        ),
        embedding_base_url="http://127.0.0.1:1/v1",
        embedding_model="shared-token-model",
        embedding_dimension=1024,
        embedding_request_dimensions=True,
    )

    pool = registry.get("agent_online_embedding_provider").backend.backend
    assert isinstance(pool, AsyncEmbeddingTokenPoolProvider)
    assert pool.lane_count == 3
    assert pool.max_failover_attempts == 2


def test_one_token_lane_capacity_state_is_shared_across_two_pods() -> None:
    from app.infra.public.embedding.async_circuit_breaker_provider import (
        AsyncCircuitBreakerEmbeddingProvider,
    )

    class DurableCapacityStore:
        def __init__(self) -> None:
            self._states: dict[str, tuple[str, float]] = {}
            self.observed_keys: list[str] = []

        def observe(self, key: str) -> tuple[str, float]:
            self.observed_keys.append(key)
            return self._states.get(key, ("available", 0.0))

        def mark_exhausted(
            self,
            key: str,
            *,
            blocked_until_epoch_seconds: float,
            reason_code: str,
        ) -> tuple[str, float]:
            self._states[key] = (reason_code, blocked_until_epoch_seconds)
            return self._states[key]

    backend = FakeAsyncLane("shared-lane")
    capacity = DurableCapacityStore()
    capacity.mark_exhausted(
        "model:token-lane-1",
        blocked_until_epoch_seconds=2000.0,
        reason_code="embedding_quota_exhausted",
    )

    def make_pod():
        lane = AsyncCircuitBreakerEmbeddingProvider(
            backend,
            clock=lambda: 1000.0,
            capacity_store=capacity,
            capacity_key="model:token-lane-1",
        )
        return _AsyncEmbeddingTokenPoolProvider([lane])

    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider as _AsyncEmbeddingTokenPoolProvider,
    )

    pod_one = make_pod()
    pod_two = make_pod()

    async def scenario() -> None:
        for pod in (pod_one, pod_two):
            with pytest.raises(EmbeddingQuotaExhaustedError) as caught:
                await pod.embed("reader")
            assert caught.value.retry_after_seconds == pytest.approx(1000.0)

    asyncio.run(scenario())
    assert backend.calls == []
    assert capacity.observed_keys == ["model:token-lane-1"] * 2

def test_dev_composition_uses_the_same_shared_token_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Dev and runtime must both expose native-async shared token lanes.

    This protects real-model performance validation from silently falling back
    to a single-token synchronous provider or the deterministic fake provider.
    """

    from app.bootstrap.opensource import build_opensource_provider_registry
    from app.infra.public.embedding.async_batching_embedding_provider import (
        AsyncBatchingEmbeddingProvider,
    )
    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )
    from app.infra.public.embedding.cached_async_embedding_provider import (
        CachedAsyncEmbeddingProvider,
    )

    for name in (
        "EMBEDDING_AUTH_TOKEN",
        "EMBEDDING_AUTH_TOKENS",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIMENSION",
        "EMBEDDING_REQUEST_DIMENSIONS",
        "EMBEDDING_QUOTA_MAX_CALLS_PER_MINUTE",
        "LLM_BASE_URL",
        "LLM_AUTH_TOKEN",
        "RERANKER_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMBEDDING_AUTH_TOKENS", "token-a,token-b,token-c")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "shared-dev-model")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    monkeypatch.setenv("EMBEDDING_REQUEST_DIMENSIONS", "true")
    monkeypatch.setenv("BCSFUSE_DATABASE_SQLITE_PATH", str(tmp_path / "workers.sqlite"))
    monkeypatch.setenv("BCSFUSE_VECTOR_SQLITE_PATH", str(tmp_path / "vectors.sqlite"))
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setenv("BCSFUSE_OBJECT_STORAGE_DIR", str(tmp_path / "objects"))
    monkeypatch.setenv("QDRANT_MODE", "local")
    monkeypatch.setenv("QDRANT_COLLECTION_NAME", "token-pool-dev-test")

    registry = build_opensource_provider_registry("dev")
    provider = registry.get("agent_online_embedding_provider")

    assert isinstance(provider, CachedAsyncEmbeddingProvider)
    assert isinstance(provider.backend, AsyncBatchingEmbeddingProvider)
    pool = provider.backend.backend
    assert isinstance(pool, AsyncEmbeddingTokenPoolProvider)
    assert pool.lane_count == 3
    assert provider.dimension == 1024


def test_async_pool_fails_over_to_another_token_after_timeout_cancellation() -> None:
    """A transport-timeout cancellation is a lane failure, not a pool abort."""

    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    class CancelledAsyncLane(FakeAsyncLane):
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.calls.extend(texts)
            raise asyncio.CancelledError

    timed_out = CancelledAsyncLane("timed-out")
    healthy = FakeAsyncLane("healthy")
    provider = AsyncEmbeddingTokenPoolProvider([timed_out, healthy])

    vector = asyncio.run(provider.embed("reader"))

    assert vector == healthy.vector
    assert timed_out.calls == ["reader"]
    assert healthy.calls == ["reader"]


def test_async_pool_preserves_caller_cancellation_without_failing_over() -> None:
    """Cancellation of the pool's host task must stop all model calls immediately."""

    from app.infra.public.embedding.async_embedding_token_pool_provider import (
        AsyncEmbeddingTokenPoolProvider,
    )

    class BlockingFirstLane(FakeAsyncLane):
        def __init__(self) -> None:
            super().__init__("first")
            self.arrived = asyncio.Event()

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            self.calls.extend(texts)
            self.arrived.set()
            await asyncio.Event().wait()

    first = BlockingFirstLane()
    second = FakeAsyncLane("second")
    provider = AsyncEmbeddingTokenPoolProvider([first, second])

    async def scenario() -> None:
        requested = asyncio.create_task(provider.embed("reader"))
        await first.arrived.wait()
        requested.cancel()
        await asyncio.sleep(0)

        with pytest.raises(asyncio.CancelledError):
            await requested

    asyncio.run(scenario())

    assert first.calls == ["reader"]
    assert second.calls == []
