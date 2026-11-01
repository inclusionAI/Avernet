"""Availability contract for bounded, non-blocking readiness probes."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry


@pytest.fixture(autouse=True)
def _disabled_profile_index(monkeypatch: pytest.MonkeyPatch):
    from app.infra.config.feature_flags import FeatureFlags

    FeatureFlags.reset()
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags._get_settings()
    yield
    FeatureFlags.reset()


class StartupProvider:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class ReadinessVectorStore:
    def __init__(
        self,
        *,
        dependency_delay_seconds: float = 0.0,
        size_delay_seconds: float = 0.0,
    ) -> None:
        self.dependency_delay_seconds = dependency_delay_seconds
        self.size_delay_seconds = size_delay_seconds
        self.dependency_calls = 0
        self.size_calls = 0
        self.connection = type("Connection", (), {"mode": "server"})()
        self.keyword_connection = type("Connection", (), {"mode": "server"})()
        self.collection_name = "worker_profiles"
        self.index_owner = "pod-a"
        self.index_version = 1
        self.index_lag_seconds = 0.0
        self.index_stale = False
        self.index_state_unavailable = False
        self.read_only = False

    def index_dependency_readiness(self) -> dict[str, bool]:
        self.dependency_calls += 1
        if self.dependency_delay_seconds:
            time.sleep(self.dependency_delay_seconds)
        return {
            "dense_qdrant_ready": True,
            "keyword_qdrant_ready": True,
        }

    def size(self) -> int:
        self.size_calls += 1
        if self.size_delay_seconds:
            time.sleep(self.size_delay_seconds)
        return 1


def _build_context(vector_store: ReadinessVectorStore) -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", vector_store)
    return ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )


def test_first_slow_dependency_probe_times_out_quickly_and_explicitly() -> None:
    store = ReadinessVectorStore(dependency_delay_seconds=0.40)
    app = create_bcsfuse_app(_build_context(store))
    app.state.readiness_probe_cache.max_probe_seconds = 0.05

    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.get("/ready")

    elapsed = time.perf_counter() - started
    body = response.json()
    assert elapsed < 0.30, f"readiness probe blocked for {elapsed:.3f}s"
    assert response.status_code == 503
    assert body["ready"] is False
    assert body["readiness_probe_timeout"] is True
    assert body["readiness_probe_inflight"] is True
    assert "dependency_delay_seconds" not in response.text


def test_cached_readiness_is_served_without_a_second_dependency_probe() -> None:
    store = ReadinessVectorStore()
    app = create_bcsfuse_app(_build_context(store))

    with TestClient(app) as client:
        first = client.get("/ready")
        assert first.status_code == 200
        assert first.json()["readiness_result_cached"] is False

        started = time.perf_counter()
        second = client.get("/ready")
        elapsed = time.perf_counter() - started

    assert second.status_code == 200
    assert second.json()["ready"] is True
    assert second.json()["readiness_result_cached"] is True
    assert elapsed < 0.20
    assert store.dependency_calls == 1


def test_slow_index_size_probe_does_not_block_the_event_loop() -> None:
    store = ReadinessVectorStore(size_delay_seconds=0.40)
    app = create_bcsfuse_app(_build_context(store))
    app.state.readiness_probe_cache.max_probe_seconds = 0.05

    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.get("/ready")

    elapsed = time.perf_counter() - started
    body: dict[str, Any] = response.json()
    assert elapsed < 0.30, f"index size probe blocked readiness for {elapsed:.3f}s"
    assert response.status_code == 503
    assert body["ready"] is False
    assert body["readiness_probe_timeout"] is True
    assert body["readiness_probe_inflight"] is True


def test_literal_snapshot_publication_failure_is_degraded_but_not_not_ready() -> None:
    from types import SimpleNamespace

    store = ReadinessVectorStore()
    store.literal_fallback_index = SimpleNamespace(
        source="durable",
        snapshot_index_version=1,
        profile_count=1,
    )
    store.snapshot_publish_error_type = "OSError"
    app = create_bcsfuse_app(_build_context(store))

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["literal_fallback_source"] == "durable"
    assert body["literal_fallback_index_version"] == 1
    assert body["literal_fallback_profiles"] == 1
    assert body["literal_fallback_publish_error_type"] == "OSError"
    assert body["degraded"] is True
