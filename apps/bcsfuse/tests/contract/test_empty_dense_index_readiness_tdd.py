"""Readiness contract for an empty or uncountable dense index."""

from __future__ import annotations

import pytest
from typing import Any

from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry


@pytest.fixture(autouse=True)
def disable_background_index(monkeypatch):
    """Health-route tests isolate readiness from optional startup reconciliation."""
    from app.infra.config.feature_flags import FeatureFlags

    FeatureFlags.reset()
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags._get_settings()
    yield
    FeatureFlags.reset()


class EmptyDenseVectorStore:
    """Simulate Qdrant probing successfully while the dense collection is empty."""

    def index_dependency_readiness(self) -> dict[str, bool]:
        return {
            "dense_qdrant_ready": True,
            "keyword_qdrant_ready": True,
            "index_state_ready": True,
            "embedding_index_signature_ready": True,
        }

    def size(self) -> int:
        return 0


class NonEmptyDenseVectorStore(EmptyDenseVectorStore):
    def size(self) -> int:
        return 2


class UncountableDenseVectorStore(EmptyDenseVectorStore):
    def size(self) -> int:
        raise RuntimeError("collection count failed")


def _context(vector_store: Any) -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())

    class Startup:
        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    registry.register("startup_provider", Startup())
    registry.register("vector_store", vector_store)
    return ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )


def _ready(vector_store: Any) -> tuple[int, dict[str, Any]]:
    with TestClient(create_bcsfuse_app(_context(vector_store))) as client:
        response = client.get("/ready")
    return response.status_code, response.json()


def test_empty_dense_index_is_not_ready(monkeypatch: Any) -> None:
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "true")
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: True),
    )
    status, body = _ready(EmptyDenseVectorStore())
    assert status == 503
    assert body["ready"] is False
    assert body["index_size"] == 0
    assert body["dense_index_empty"] is True


def test_nonempty_dense_index_remains_ready(monkeypatch: Any) -> None:
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: True),
    )
    status, body = _ready(NonEmptyDenseVectorStore())
    assert status == 200
    assert body["ready"] is True
    assert body["index_size"] == 2
    assert body["dense_index_empty"] is False


def test_uncountable_dense_index_is_not_ready(monkeypatch: Any) -> None:
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: True),
    )
    status, body = _ready(UncountableDenseVectorStore())
    assert status == 503
    assert body["ready"] is False
    assert body["index_size"] is None
    assert body["index_size_unavailable"] is True


def test_empty_index_is_ready_when_profile_embedding_index_is_disabled(
    monkeypatch: Any,
) -> None:
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: False),
    )
    status, body = _ready(EmptyDenseVectorStore())
    assert status == 200
    assert body["ready"] is True
    assert body["index_size"] == 0
    assert body["dense_index_empty"] is False
