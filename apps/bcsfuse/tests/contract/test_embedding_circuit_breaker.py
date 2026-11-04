"""The embedding circuit breaker protects capacity and enables lexical fallback."""

from __future__ import annotations

import threading

import pytest
from prometheus_client import REGISTRY


class FailingBackend:
    dimension = 64
    model = "circuit-embedding"
    closed = False

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fail:
            self.entered.set()
            self.release.wait(timeout=2)
            raise RuntimeError("embedding unavailable")
        return [1.0, 0.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.embed("\0".join(texts))
        return [[1.0, 0.0] for _ in texts]

    def close(self):
        self.closed = True


def breaker(backend, **kwargs):
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )

    return CircuitBreakerEmbeddingProvider(backend, **kwargs)


def sample(name: str, labels: dict[str, str] | None = None) -> float:
    value = REGISTRY.get_sample_value(name, labels or {})
    return 0.0 if value is None else float(value)


def test_consecutive_failures_open_circuit_and_block_backend_calls():
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingCircuitOpenError,
    )

    backend = FailingBackend(fail=True)
    provider = breaker(backend, failure_threshold=2, recovery_timeout_seconds=10)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="embedding unavailable"):
            provider.embed("first")

    assert len(backend.calls) == 2
    with pytest.raises(EmbeddingCircuitOpenError):
        provider.embed("short-circuited")
    assert len(backend.calls) == 2
    assert sample(
        "bcsfuse_embedding_circuit_rejected_total", {"model": backend.model}
    ) >= 1
    assert sample("bcsfuse_embedding_circuit_open", {"model": backend.model}) == 1


def test_recovery_probe_success_closes_circuit():
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingCircuitOpenError,
    )

    class Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    backend = FailingBackend(fail=True)
    provider = breaker(
        backend,
        failure_threshold=1,
        recovery_timeout_seconds=1,
        clock=Clock(),
    )

    with pytest.raises(RuntimeError):
        provider.embed("failure")
    provider.backend.fail = False
    provider.clock.now = 1

    assert provider.embed("recovered") == [1.0, 0.0]
    assert provider.embed("healthy") == [1.0, 0.0]
    assert sample("bcsfuse_embedding_circuit_open", {"model": backend.model}) == 0


def test_failed_recovery_probe_stays_open_and_resets_timer():
    class Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    backend = FailingBackend(fail=True)
    provider = breaker(
        backend,
        failure_threshold=1,
        recovery_timeout_seconds=1,
        clock=Clock(),
    )

    with pytest.raises(RuntimeError):
        provider.embed("failure")
    provider.clock.now = 1
    with pytest.raises(RuntimeError):
        provider.embed("failed probe")
    provider.backend.fail = False
    provider.clock.now = 1.5
    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingCircuitOpenError,
    )

    with pytest.raises(EmbeddingCircuitOpenError):
        provider.embed("still open")
    provider.clock.now = 2.1
    assert provider.embed("recovered") == [1.0, 0.0]


def test_half_open_probe_is_single_flight(monkeypatch):
    class Gate:
        def __init__(self):
            self.call = threading.Event()
            self.release = threading.Event()

        def embed(self, text: str):
            self.call.set()
            self.release.wait(timeout=2)
            return [1.0, 0.0]

    class Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

    gate = Gate()
    gate.embed_batch = lambda texts: [gate.embed("batch") for _ in texts]
    gate.model = "circuit-embedding"
    provider = breaker(
        gate,
        failure_threshold=1,
        recovery_timeout_seconds=1,
        clock=Clock(),
    )
    provider.state = "open"
    provider._state_changed_at = 0
    provider.clock.now = 1

    from app.infra.public.embedding.circuit_breaker_provider import (
        EmbeddingCircuitOpenError,
    )

    probe = threading.Thread(target=lambda: provider.embed("probe"))
    probe.start()
    assert gate.call.wait(timeout=2)
    with pytest.raises(EmbeddingCircuitOpenError):
        provider.embed("second")
    gate.release.set()
    probe.join(timeout=2)
    assert not probe.is_alive()


def test_empty_batch_does_not_touch_circuit_or_backend():
    backend = FailingBackend(fail=True)
    provider = breaker(
        backend,
        failure_threshold=1,
        recovery_timeout_seconds=1,
    )

    assert provider.embed_batch([]) == []
    assert backend.calls == []
    assert provider.state == "closed"


def test_runtime_composition_inserts_breaker_between_cache_and_real_backend(
    monkeypatch,
):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("EMBEDDING_MODEL", "circuit-composition")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )
    from app.infra.public.embedding.cached_embedding_provider import (
        CachedEmbeddingProvider,
    )
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )
    from app.infra.public.embedding.embedding_token_pool_provider import (
        EmbeddingTokenPoolProvider,
    )
    from app.infra.public.embedding.real_embedding_provider import (
        RealEmbeddingProvider,
    )
    from app.bootstrap.provider_registry import ProviderRegistry

    class Config:
        def get(self, key, default=None):
            return {
                "embedding.circuit.failure_threshold": 4,
                "embedding.circuit.recovery_seconds": 12.0,
                "embedding.cache.max_entries": 10000,
                "embedding.cache.ttl_seconds": 300.0,
            }.get(key, default)

        def get_int(self, key, default=None):
            values = {
                "embedding.circuit.failure_threshold": 4,
                "embedding.cache.max_entries": 10000,
            }
            return values.get(key, default)

        def get_float(self, key, default=None):
            values = {
                "embedding.circuit.recovery_seconds": 12.0,
                "embedding.cache.ttl_seconds": 300.0,
            }
            return values.get(key, default)

    registry = ProviderRegistry()
    register_runtime_service_providers(registry, Config())
    provider = registry.get("embedding_provider")

    assert isinstance(provider, CachedEmbeddingProvider)
    assert isinstance(provider, RealEmbeddingProvider)
    assert isinstance(provider.backend, EmbeddingTokenPoolProvider)
    circuit = provider.backend.lanes[0]
    assert isinstance(circuit, CircuitBreakerEmbeddingProvider)
    assert circuit.failure_threshold == 4
    assert circuit.recovery_timeout_seconds == 12.0

def test_yaml_config_maps_embedding_circuit_environment_overrides(monkeypatch):
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("EMBEDDING_MODEL", "circuit-env-override")
    monkeypatch.setenv("EMBEDDING_CIRCUIT_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("EMBEDDING_CIRCUIT_RECOVERY_SECONDS", "11")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    config = YamlEnvConfigProvider(config_path="/tmp/nonexistent-bcsfuse.yaml")
    registry = ProviderRegistry()
    register_runtime_service_providers(registry, config)

    provider = registry.get("embedding_provider").backend.lanes[0]

    assert isinstance(provider, CircuitBreakerEmbeddingProvider)
    assert provider.failure_threshold == 2
    assert provider.recovery_timeout_seconds == 11.0

def test_cached_query_still_succeeds_while_circuit_is_open():
    from search_core.config.embedding import EmbeddingSettings

    from app.infra.public.embedding.cached_embedding_provider import (
        CachedEmbeddingProvider,
    )
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )

    settings = EmbeddingSettings(
        base_url="http://127.0.0.1:1/v1",
        auth_token="test-only",
        model="circuit-cache-embedding",
        dimension=64,
    )
    backend = FailingBackend()
    backend._settings = settings
    provider = CachedEmbeddingProvider(
        CircuitBreakerEmbeddingProvider(
            backend,
            failure_threshold=1,
            recovery_timeout_seconds=10,
        ),
        max_entries=10,
        ttl_seconds=60,
    )

    assert provider.embed("cached query") == [1.0, 0.0]
    backend.fail = True
    with pytest.raises(RuntimeError, match="embedding unavailable"):
        provider.embed("uncached query")

    assert provider.embed("cached query") == [1.0, 0.0]
    assert provider.backend.state == "open"
