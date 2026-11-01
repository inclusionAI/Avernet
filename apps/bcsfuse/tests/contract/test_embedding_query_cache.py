from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry

from app.infra.embedding.config.embedding_settings import EmbeddingSettings
from app.infra.public.embedding.real_embedding_provider import RealEmbeddingProvider


class Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class RecordingEmbeddingProvider(RealEmbeddingProvider):
    def __init__(self, *, fail_first: bool = False):
        super().__init__(
            settings=EmbeddingSettings(
                base_url="http://127.0.0.1:1/v1",
                auth_token="test-only",
                model=f"embedding-cache-{id(self)}",
                dimension=64,
            ),
            max_retries=0,
        )
        self.calls: list[str] = []
        self.fail_first = fail_first
        self.failed_once = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return self._embed_one(text, len(self.calls))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.extend(texts)
        if self.release is not None:
            self.entered.set()
            self.release.wait(timeout=2)
            self.release = None

        if self.fail_first and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("backend unavailable")

        return [self._embed_one(text, index + 1) for index, text in enumerate(texts)]

    def _embed_one(self, text: str, call_index: int) -> list[float]:
        return [float(len(text) + call_index), 1.0]


def _provider(backend, **kwargs):
    from app.infra.public.embedding.cached_embedding_provider import (
        CachedEmbeddingProvider,
    )

    return CachedEmbeddingProvider(backend, **kwargs)


def test_repeated_normalized_queries_call_backend_once():
    backend = RecordingEmbeddingProvider()
    provider = _provider(backend)

    whitespace_first = provider.embed("  支付\n\t助手  ")
    whitespace_second = provider.embed("支付 助手")
    unicode_first = provider.embed("ﬁx bug")
    unicode_second = provider.embed("fix bug")

    assert whitespace_first == whitespace_second
    assert unicode_first == unicode_second
    assert backend.calls == ["  支付\n\t助手  ", "ﬁx bug"]
    stats = provider.stats
    assert stats["cache_entries"] == 2
    assert stats["cache_hits"] == 2
    assert stats["cache_hit_ratio"] > 0


def test_expired_entries_are_refreshed_once_ttl_elapses():
    backend = RecordingEmbeddingProvider()
    clock = Clock()
    provider = _provider(backend, ttl_seconds=60, clock=clock)

    provider.embed("支付")
    clock.advance(61)
    provider.embed("支付")

    assert len(backend.calls) == 2
    assert provider.stats["cache_hits"] == 0
    assert provider.stats["cache_misses"] == 2


def test_lru_capacity_evicts_the_oldest_query_before_refetch():
    backend = RecordingEmbeddingProvider()
    provider = _provider(backend, max_entries=1)

    provider.embed("first")
    provider.embed("second")
    provider.embed("first")

    assert backend.calls == ["first", "second", "first"]
    assert provider.stats["cache_evictions"] == 2


def test_concurrent_identical_queries_share_one_backend_call():
    backend = RecordingEmbeddingProvider()
    provider = _provider(backend)
    results: dict[str, list[float]] = {}
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def call(name: str) -> None:
        barrier.wait(timeout=2)
        vector = provider.embed("same query")
        with lock:
            results[name] = vector

    first = threading.Thread(target=call, args=("first",))
    second = threading.Thread(target=call, args=("second",))
    first.start()
    second.start()

    assert backend.entered.wait(timeout=2)
    backend.release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(backend.calls) == 1
    assert results["first"] == results["second"]


def test_failed_backend_calls_are_not_cached():
    backend = RecordingEmbeddingProvider(fail_first=True)
    provider = _provider(backend)

    try:
        provider.embed("unavailable query")
    except RuntimeError:
        pass
    else:
        raise AssertionError("first backend failure was swallowed")

    provider.embed("unavailable query")

    assert len(backend.calls) == 2
    assert provider.stats["cache_errors"] == 1
    assert provider.stats["cache_entries"] == 1


def test_runtime_composition_registers_cached_real_embedding_provider(monkeypatch):
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )
    from app.infra.public.embedding.cached_embedding_provider import (
        CachedEmbeddingProvider,
    )

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    registry = ProviderRegistry()
    register_runtime_service_providers(registry, YamlEnvConfigProvider())
    provider = registry.get("embedding_provider")

    assert isinstance(provider, RealEmbeddingProvider)
    assert isinstance(provider, CachedEmbeddingProvider)
    assert provider.cache_max_entries > 0
    assert provider.cache_ttl_seconds > 0


def test_runtime_composition_binds_configured_query_batch_size(monkeypatch):
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    class Config:
        def get(self, key, default=None):
            return default

        def get_int(self, key, default=None):
            if key == "embedding.cache.max_query_batch_size":
                return 12
            return default

        def get_float(self, key, default=None):
            return default

    registry = ProviderRegistry()
    register_runtime_service_providers(registry, Config())
    provider = registry.get("embedding_provider")

    assert provider.max_query_batch_size == 12


def test_metrics_endpoint_exposes_embedding_cache_metrics():
    class StartupProvider:
        async def initialize(self):
            return None

        async def shutdown(self):
            return None

    class ReadyVectorStore:
        connection = type("Connection", (), {"mode": "server"})()
        collection_name = "worker_profiles"
        index_owner = "pod-a"
        index_version = 1
        index_lag_seconds = 0
        index_stale = False
        read_only = False

        def check_ready(self):
            return True

        @property
        def path(self):
            return "/tmp/embedding-cache-metrics"

    provider = _provider(RecordingEmbeddingProvider())
    provider.embed("metrics query")

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", ReadyVectorStore())
    app = create_bcsfuse_app(
        ApplicationContext(mode="runtime", startup_profile="opensource", registry=registry)
    )

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    for metric_name in [
        "bcsfuse_embedding_cache_hits_total",
        "bcsfuse_embedding_cache_misses_total",
        "bcsfuse_embedding_cache_errors_total",
        "bcsfuse_embedding_cache_evictions_total",
        "bcsfuse_embedding_cache_entries",
        "bcsfuse_embedding_batch_calls_total",
        "bcsfuse_embedding_batched_queries_total",
        "bcsfuse_embedding_batch_size",
    ]:
        assert metric_name in response.text


def test_runtime_composition_binds_query_batch_workers(monkeypatch):
    from app.bootstrap.opensource import register_runtime_service_providers
    from app.bootstrap.provider_registry import ProviderRegistry

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    class Config:
        def get(self, key, default=None):
            return default

        def get_int(self, key, default=None):
            if key == "embedding.cache.max_query_batch_workers":
                return 3
            return default

        def get_float(self, key, default=None):
            return default

    registry = ProviderRegistry()
    register_runtime_service_providers(registry, Config())
    provider = registry.get("embedding_provider")

    assert provider.max_query_batch_workers == 3
