"""Shared embedding-capacity degradation contracts."""

from __future__ import annotations

import threading
import time

import pytest


class FakeResponse:
    def __init__(self, retry_after: str | None = None) -> None:
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}


class FailingBackend:
    dimension = 64
    model = "shared-capacity-embedding"
    closed = False

    def __init__(self, *, fail: bool, retry_after: str | None = None) -> None:
        self.fail = fail
        self.retry_after = retry_after
        self.calls = 0
        self.lock = threading.Lock()

    def _record_call(self) -> None:
        self.calls += 1

    def embed(self, text: str) -> list[float]:
        self._record_call()
        self._raise_when_configured()
        return [1.0, 0.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._record_call()
        self._raise_when_configured()
        return [[1.0, 0.0] for _ in texts]

    def _raise_when_configured(self) -> None:
        if not self.fail:
            return
        from search_core.providers.embedding import EmbeddingAPIError

        raise EmbeddingAPIError(
            "embedding request quota exceeded",
            status_code=429,
            response=FakeResponse(self.retry_after),
        )

    def close(self) -> None:
        self.closed = True


class MemoryCapacityStore:
    def __init__(self) -> None:
        self._states: dict[str, tuple[str, float]] = {}
        self.fail = False

    def observe(self, model: str) -> tuple[str, float]:
        if self.fail:
            raise RuntimeError("capacity store unavailable")
        return self._states.get(model, ("available", 0.0))

    def mark_exhausted(
        self,
        model: str,
        *,
        blocked_until_epoch_seconds: float,
        reason_code: str = "quota_exhausted",
    ) -> tuple[str, float]:
        if self.fail:
            raise RuntimeError("capacity store unavailable")
        current = self._states.get(model, ("available", 0.0))
        if blocked_until_epoch_seconds > float(current[1]):
            self._states[model] = (reason_code, blocked_until_epoch_seconds)
        return self._states[model]


def make_provider(backend, store):
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )

    return CircuitBreakerEmbeddingProvider(
        backend,
        failure_threshold=2,
        recovery_timeout_seconds=10.0,
        capacity_store=store,
        quota_recovery_seconds=60.0,
        clock=lambda: 1000.0,
    )


def test_429_publishes_shared_exhaustion_and_blocks_second_replica():
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingQuotaExhaustedError,
    )

    store = MemoryCapacityStore()
    failing = FailingBackend(fail=True, retry_after="30")
    healthy = FailingBackend(fail=False)

    first = make_provider(failing, store)
    second = make_provider(healthy, store)

    with pytest.raises(EmbeddingQuotaExhaustedError) as captured:
        first.embed_batch(["first replica"])

    assert captured.value.status_code == 429
    assert captured.value.retry_after_seconds == 30.0

    with pytest.raises(EmbeddingQuotaExhaustedError):
        second.embed_batch(["second replica gets no backend call"])

    assert failing.calls == 1
    assert healthy.calls == 0
    assert store.observe(FailingBackend.model) == ("embedding_quota_exhausted", 1030.0)


def test_without_retry_after_uses_default_quota_window():
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingQuotaExhaustedError,
    )

    store = MemoryCapacityStore()
    backend = FailingBackend(fail=True)
    provider = make_provider(backend, store)
    provider.quota_recovery_seconds = 120.0

    with pytest.raises(EmbeddingQuotaExhaustedError) as captured:
        provider.embed("no retry header")

    assert captured.value.retry_after_seconds == 120.0
    assert store.observe(backend.model) == ("embedding_quota_exhausted", 1120.0)


def test_remote_replica_durable_block_is_honored_before_backend_call():
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingQuotaExhaustedError,
    )

    store = MemoryCapacityStore()
    store.mark_exhausted(FailingBackend.model, blocked_until_epoch_seconds=2000.0)
    backend = FailingBackend(fail=False)
    provider = make_provider(backend, store)

    with pytest.raises(EmbeddingQuotaExhaustedError) as captured:
        provider.embed_batch(["remote replica already observed 429"])

    assert backend.calls == 0
    assert captured.value.retry_after_seconds == pytest.approx(1000.0)


def test_capacity_store_unavailable_fails_open_to_backend():
    store = MemoryCapacityStore()
    backend = FailingBackend(fail=False)
    provider = make_provider(backend, store)

    store.fail = True
    assert provider.embed("capacity-store outage") == [1.0, 0.0]
    assert backend.calls == 1


def test_sqlite_store_blocks_until_new_remote_exhaustion_expiry():
    import tempfile
    from pathlib import Path

    from app.infra.public.stores.sqlite_embedding_capacity_store import (
        SQLiteEmbeddingCapacityStore,
    )

    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "capacity.sqlite")
        store = SQLiteEmbeddingCapacityStore(path)
        now = time.time()

        assert store.observe(FailingBackend.model)[0] == "available"
        store.mark_exhausted(
            FailingBackend.model,
            blocked_until_epoch_seconds=now + 10,
            reason_code="quota_exhausted",
        )
        state, expires_at = store.observe(FailingBackend.model)
        assert state == "quota_exhausted"
        assert expires_at == pytest.approx(now + 10)

        store.mark_exhausted(
            FailingBackend.model,
            blocked_until_epoch_seconds=now + 2,
            reason_code="quota_exhausted",
        )
        assert store.observe(FailingBackend.model)[1] == pytest.approx(now + 10)
        store.mark_exhausted(
            FailingBackend.model,
            blocked_until_epoch_seconds=now + 20,
            reason_code="quota_exhausted",
        )
        assert store.observe(FailingBackend.model)[1] == pytest.approx(now + 20)


def test_provider_composition_uses_shared_capacity_store(monkeypatch):
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("EMBEDDING_MODEL", "shared-capacity-composition")
    monkeypatch.setenv("EMBEDDING_CIRCUIT_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("EMBEDDING_CIRCUIT_RECOVERY_SECONDS", "10")
    monkeypatch.setenv("EMBEDDING_QUOTA_RECOVERY_SECONDS", "45")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    config = YamlEnvConfigProvider(config_path="/tmp/nonexistent-bcsfuse.yaml")
    registry = ProviderRegistry()
    store = MemoryCapacityStore()
    registry.register("embedding_capacity_store", store)
    register_runtime_service_providers(registry, config)

    circuit = registry.get("embedding_provider").backend
    assert circuit.capacity_store is store
    assert circuit.quota_recovery_seconds == 45.0


def test_mysql_capacity_store_construction_is_lazy(monkeypatch) -> None:
    from app.infra.public.stores import mysql_embedding_capacity_store as module

    ensure_schema = (
        module.MySQLEmbeddingCapacityStore._ensure_schema
    )
    called = []

    def fail_if_initialized(*args: object, **kwargs: object) -> None:
        called.append("schema")
        raise AssertionError("composition must not open the capacity database")

    monkeypatch.setattr(
        module.MySQLEmbeddingCapacityStore,
        "_ensure_schema",
        fail_if_initialized,
    )
    try:
        module.MySQLEmbeddingCapacityStore(connection_pool=object())
    finally:
        monkeypatch.setattr(
            module.MySQLEmbeddingCapacityStore,
            "_ensure_schema",
            ensure_schema,
        )

    assert called == []


def test_cluster_quota_exhaustion_has_a_distinct_delivery_reason() -> None:
    import asyncio

    from app.application.agent_search_service import (
        AgentSearchCommand,
        AgentSearchService,
        degraded_reasons,
    )
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingQuotaExhaustedError,
    )

    class QuotaExhaustedGenerator:
        def embed(self, query: str) -> list[float]:
            raise EmbeddingQuotaExhaustedError(
                "model quota exhausted",
                retry_after_seconds=30.0,
            )

    class KeywordFallback:
        def match(self, **kwargs: dict) -> list[object]:
            return [{"profile_key": "bot:reader:default", "score": 0.1}]

    outcome = asyncio.run(
        AgentSearchService(
            embedding_generator=QuotaExhaustedGenerator(),
            vector_match_service=KeywordFallback(),
            candidate_recommendation_service=None,
        ).search(AgentSearchCommand(query="reader", top_k=1))
    )

    assert outcome.metadata["semantic_reason_code"] == "embedding_quota_exhausted"
    assert outcome.metadata["semantic_retry_after_seconds"] == 30.0
    assert "embedding_quota_exhausted" in degraded_reasons(outcome.metadata)


def test_repeated_embedding_outage_warnings_are_rate_limited(monkeypatch, caplog) -> None:
    import logging

    from app.domain.services.retrieval_logging_rate import warn_once_per_interval

    now = [100.0]
    monkeypatch.setattr(
        "app.domain.services.retrieval_logging_rate.time.monotonic",
        lambda: now[0],
    )
    logger = logging.getLogger("bcsfuse-retrieval-warning-rate")
    logger.setLevel(logging.WARNING)

    with caplog.at_level(logging.WARNING, logger=logger.name):
        clock=lambda: now[0]
        assert warn_once_per_interval(
            logger,
            "embedding_error_test",
            "embedding degraded event",
            interval_seconds=5,
            clock=clock,
        )
        assert not warn_once_per_interval(
            logger,
            "embedding_error_test",
            "embedding degraded event",
            interval_seconds=5,
            clock=clock,
        )
        now[0] += 5
        assert warn_once_per_interval(
            logger,
            "embedding_error_test",
            "embedding degraded event",
            interval_seconds=5,
            clock=clock,
        )

    assert len([r for r in caplog.records if r.name == logger.name]) == 2


class SharedRateLimitStore(MemoryCapacityStore):
    def __init__(self, *, fail_reserve: bool = False) -> None:
        super().__init__()
        self.reserve_calls = 0
        self.fail_reserve = fail_reserve

    def reserve(
        self,
        model: str,
        *,
        now_epoch_seconds: float,
        max_calls: int,
        period_seconds: float,
    ) -> tuple[bool, float, str]:
        self.reserve_calls += 1
        if self.fail_reserve:
            raise RuntimeError("capacity store unavailable")
        if max_calls <= 0 or period_seconds <= 0:
            return (True, 0.0, "available")
        state = self._states.setdefault(
            model, {"calls": 0, "window_start": now_epoch_seconds}
        )
        if now_epoch_seconds < state["window_start"] + period_seconds and (
            now_epoch_seconds < state["window_start"]
            or state["calls"] >= max_calls
        ):
            return (
                False,
                max(0.001, state["window_start"] + period_seconds - now_epoch_seconds),
                "embedding_rate_limit_admission_rejected",
            )
        if now_epoch_seconds >= state["window_start"] + period_seconds:
            state["window_start"] = now_epoch_seconds
            state["calls"] = 0
        state["calls"] += 1
        return (True, 0.0, "available")


def make_rate_limited_provider(backend, store, *, max_calls_per_minute: int):
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )

    return CircuitBreakerEmbeddingProvider(
        backend,
        failure_threshold=2,
        recovery_timeout_seconds=10.0,
        capacity_store=store,
        quota_recovery_seconds=60.0,
        max_calls_per_minute=max_calls_per_minute,
        clock=lambda: 1000.0,
    )


def test_cluster_rate_limit_admission_rejects_before_backend_call() -> None:
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingQuotaExhaustedError,
    )

    store = SharedRateLimitStore()
    backend = FailingBackend(fail=False)
    provider = make_rate_limited_provider(
        backend, store, max_calls_per_minute=2
    )

    assert provider.embed("first admitted model call") == [1.0, 0.0]
    assert provider.embed_batch(["second admitted model call"]) == [[1.0, 0.0]]
    with pytest.raises(EmbeddingQuotaExhaustedError) as captured:
        provider.embed("third call must not reach the model")

    assert backend.calls == 2
    assert captured.value.reason_code == (
        "embedding_rate_limit_admission_rejected"
    )
    assert captured.value.retry_after_seconds == pytest.approx(60.0)


def test_cluster_rate_limit_admission_is_shared_by_replicas() -> None:
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingQuotaExhaustedError,
    )

    store = SharedRateLimitStore()
    first_backend = FailingBackend(fail=False)
    second_backend = FailingBackend(fail=False)
    first = make_rate_limited_provider(
        first_backend, store, max_calls_per_minute=1
    )
    second = make_rate_limited_provider(
        second_backend, store, max_calls_per_minute=1
    )

    assert first.embed("first replica consumes the shared call")
    with pytest.raises(EmbeddingQuotaExhaustedError):
        second.embed("second replica observes the shared budget")

    assert first_backend.calls == 1
    assert second_backend.calls == 0


def test_cluster_rate_limit_store_failure_fails_open_to_backend() -> None:
    store = SharedRateLimitStore(fail_reserve=True)
    backend = FailingBackend(fail=False)
    provider = make_rate_limited_provider(
        backend, store, max_calls_per_minute=1
    )

    assert provider.embed("quota coordination outage") == [1.0, 0.0]
    assert backend.calls == 1


def test_runtime_composes_cluster_call_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("EMBEDDING_MODEL", "shared-rate-limit-composition")
    monkeypatch.setenv("EMBEDDING_QUOTA_MAX_CALLS_PER_MINUTE", "7")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    registry = ProviderRegistry()
    store = SharedRateLimitStore()
    registry.register("embedding_capacity_store", store)
    config = YamlEnvConfigProvider(config_path="/tmp/nonexistent-bcsfuse.yaml")
    register_runtime_service_providers(registry, config)

    circuit = registry.get("embedding_provider").backend
    assert circuit.max_calls_per_minute == 7
    assert circuit.quota_period_seconds == 60.0


def test_sqlite_capacity_store_reserves_an_atomic_call_window(tmp_path) -> None:
    from app.infra.public.stores.sqlite_embedding_capacity_store import (
        SQLiteEmbeddingCapacityStore,
    )

    store = SQLiteEmbeddingCapacityStore(str(tmp_path / "capacity.sqlite"))
    model = FailingBackend.model

    assert store.reserve(
        model, now_epoch_seconds=100.0, max_calls=2, period_seconds=60.0
    ) == (True, 0.0, "available")
    assert store.reserve(
        model, now_epoch_seconds=101.0, max_calls=2, period_seconds=60.0
    ) == (True, 0.0, "available")
    allowed, retry_after, reason = store.reserve(
        model, now_epoch_seconds=102.0, max_calls=2, period_seconds=60.0
    )
    assert (allowed, reason) == (
        False,
        "embedding_rate_limit_admission_rejected",
    )
    assert retry_after == pytest.approx(58.0)
    assert store.reserve(
        model, now_epoch_seconds=161.0, max_calls=2, period_seconds=60.0
    ) == (True, 0.0, "available")
