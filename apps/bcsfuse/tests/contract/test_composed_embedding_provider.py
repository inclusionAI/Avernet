"""Host embedding providers must reach TC indexing and query consumers."""

import asyncio
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.adapters.http.dependencies import fusion_dependencies
from app.bootstrap.app_factory import (
    _configure_fusion_dependencies,
    create_bcsfuse_app,
)
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.opensource import build_opensource_provider_registry
from app.bootstrap.provider_registry import ProviderRegistry


class FixedEmbedding:
    def __init__(self, value):
        self.value = value
        self.dimension = len(value)
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        return list(self.value)


@pytest.fixture
def runtime_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("BCSFUSE_TC_V1_STORAGE", "card")
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    for key in ("EMBEDDING_BASE_URL", "EMBEDDING_AUTH_TOKEN", "EMBEDDING_MODEL"):
        monkeypatch.delenv(key, raising=False)
    composition = 0

    @contextmanager
    def open_runtime(provider):
        nonlocal composition
        composition += 1
        directory = tmp_path / str(composition)
        monkeypatch.setenv(
            "WORKER_REGISTRY_SQLITE_DB_PATH", str(directory / "tc.sqlite3")
        )
        monkeypatch.setenv("QDRANT_LOCAL_PATH", str(directory / "qdrant"))
        defaults = build_opensource_provider_registry("test")
        registry = ProviderRegistry()
        for name in defaults.keys():  # noqa: SIM118 - ProviderRegistry is not iterable
            if name != "embedding_provider":
                registry.register(name, defaults.require(name))
        if provider is not None:
            registry.register("embedding_provider", provider)
        context = ApplicationContext(
            mode="test", startup_profile="injected-host", registry=registry
        )
        with TestClient(
            create_bcsfuse_app(context),
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            yield client, context

    return open_runtime


def test_switching_compositions_replaces_embedding_and_query_cache(runtime_factory):
    for vector in ([1.0, 0.0], [0.0, 1.0]):
        provider = FixedEmbedding(vector)
        with runtime_factory(provider) as (_client, context):
            runtime = context.registry.require("tc_search")
            assert runtime.updates.embedding is provider
            assert runtime.embedding.provider is provider
            assert asyncio.run(runtime.embedding.embed("same-query")) == vector
            assert provider.calls == ["same-query"]


def test_composed_embedding_survives_service_cache_reset(runtime_factory):
    provider = FixedEmbedding([1.0, 0.0])
    with runtime_factory(provider) as (_client, context):
        runtime = context.registry.require("tc_search")
        fusion_dependencies.reset_fusion_services()
        _configure_fusion_dependencies(context)

        assert fusion_dependencies.get_candidate_recommendation_service() is runtime
        assert runtime.updates.embedding is provider
        assert runtime.embedding.provider is provider
        assert asyncio.run(runtime.embedding.embed("probe")) == provider.value


def test_host_embedding_reaches_indexing_and_recommendation(
    runtime_factory, monkeypatch
):
    from app.infra.embedding.config.embedding_settings import EmbeddingSettings

    def reject_environment_provider(*args, **kwargs):
        raise AssertionError("Composed TC must use the host embedding provider")

    monkeypatch.setattr(EmbeddingSettings, "__init__", reject_environment_provider)
    provider = FixedEmbedding([1.0, 0.0])
    with runtime_factory(provider) as (client, _context):
        response = client.post(
            "/v1/workers/bot:owner/sync",
            json={
                "name": "Database specialist",
                "description": "MySQL migrations",
                "availability": "public",
                "runtime_state": "online",
                "profile": {"profile_id": "legacy", "activate": False},
            },
        )
        assert response.status_code == 200, response.text
        assert provider.calls
        provider.calls.clear()
        response = client.post(
            "/api/v1/recommend", json={"question": "MySQL migrations"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["recommendations"][0]["worker_id"] == "bot:owner"
        assert provider.calls == ["MySQL migrations"]


def test_composed_missing_embedding_fails_without_environment_fallback(
    runtime_factory, monkeypatch
):
    from app.infra.embedding.config.embedding_settings import EmbeddingSettings

    environment_reads = []

    def reject_environment_read(*args, **kwargs):
        environment_reads.append(True)
        raise AssertionError("Missing host providers must not fall back to settings")

    monkeypatch.setattr(EmbeddingSettings, "__init__", reject_environment_read)
    with (
        pytest.raises(LookupError, match="embedding_provider"),
        runtime_factory(None),
    ):
        pytest.fail("TC started without its required embedding provider")
    assert environment_reads == []
