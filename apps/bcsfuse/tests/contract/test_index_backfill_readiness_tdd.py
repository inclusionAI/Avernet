"""Startup backfill must be a hard readiness state, not hidden by a warm cache."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
import pytest


@pytest.fixture(autouse=True)
def preserve_feature_flags_cache():
    from app.infra.config.feature_flags import FeatureFlags

    original_settings = FeatureFlags._settings
    yield
    FeatureFlags._settings = original_settings


class StartupProvider:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class HealthyVectorStore:
    connection = type("Connection", (), {"mode": "server"})()
    keyword_connection = type("Connection", (), {"mode": "server"})()
    collection_name = "worker_profiles"
    index_owner = "pod-a"
    index_version = 1
    index_lag_seconds = 0.0
    index_stale = False
    read_only = False

    def check_ready(self) -> bool:
        return True

    def size(self) -> int:
        return 2


def build_context(backfill_status) -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", HealthyVectorStore())
    registry.register("index_backfill_status", backfill_status)
    return ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )


def ready(app):
    with TestClient(app) as client:
        return client.get("/ready")


def test_running_backfill_makes_pod_unready(monkeypatch) -> None:
    from app.bootstrap.index_backfill_status import IndexBackfillStatus
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    monkeypatch.setattr(
        FeatureFlags,
        "is_enabled",
        classmethod(lambda cls, name: False),
    )
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: False),
    )
    status = IndexBackfillStatus()
    status.mark_started()

    response = ready(create_bcsfuse_app(build_context(status)))

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["index_backfill_running"] is True
    assert body["index_backfill_failed"] is False


def test_completed_backfill_restores_readiness(monkeypatch) -> None:
    from app.bootstrap.index_backfill_status import IndexBackfillStatus
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setattr(
        FeatureFlags,
        "is_enabled",
        classmethod(lambda cls, name: False),
    )
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: False),
    )
    status = IndexBackfillStatus()
    status.mark_started()
    status.mark_completed()

    response = ready(create_bcsfuse_app(build_context(status)))

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["index_backfill_running"] is False
    assert body["index_backfill_failed"] is False


def test_failed_backfill_remains_unready(monkeypatch) -> None:
    from app.bootstrap.index_backfill_status import IndexBackfillStatus
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setattr(
        FeatureFlags,
        "is_enabled",
        classmethod(lambda cls, name: False),
    )
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: False),
    )
    status = IndexBackfillStatus()
    status.mark_failed(RuntimeError.__name__)

    response = ready(create_bcsfuse_app(build_context(status)))

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["index_backfill_failed"] is True
    assert body["index_backfill_error_type"] == "RuntimeError"


def test_background_build_exception_is_not_reported_as_completed(monkeypatch) -> None:
    import threading

    from app.bootstrap.index_backfill_status import IndexBackfillStatus
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "true")
    monkeypatch.setattr(
        FeatureFlags,
        "is_enabled",
        classmethod(lambda cls, name: True),
    )
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: True),
    )

    class RunImmediatelyThread:
        def __init__(
            self,
            *,
            target,
            daemon: bool,
            name: str,
        ) -> None:
            self.target = target
            assert daemon is True
            assert name == "bg-index-build"

        def start(self) -> None:
            # Synchronous test execution must also emulate the shutdown signal
            # that a real OS thread can observe before the worker retries.
            worker = getattr(self.target, "__self__", None)
            stop_event = getattr(worker, "_stop_event", None)
            if stop_event is not None:
                stop_event.set()
            self.target()

        def join(self) -> None:
            return None

    monkeypatch.setattr(threading, "Thread", RunImmediatelyThread)

    status = IndexBackfillStatus()
    context = build_context(status)
    app = create_bcsfuse_app(context)

    with TestClient(app):
        snapshot = status.snapshot()

    assert snapshot["failed"] is True
    assert snapshot["error_type"] == "RuntimeError"


def test_partial_index_failure_makes_the_pod_unready(monkeypatch) -> None:
    import threading

    from app.adapters.http.dependencies import fusion_dependencies
    from app.bootstrap.index_backfill_status import IndexBackfillStatus
    from app.domain.services.profile_embedding_indexer import IndexingResult
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "true")
    monkeypatch.setattr(
        FeatureFlags,
        "is_enabled",
        classmethod(lambda cls, name: True),
    )
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: True),
    )
    monkeypatch.setattr(fusion_dependencies, "_get_embedding_generator", lambda: object())

    class ProfileSource:
        def scan(self):
            return type("ScanResult", (), {"profiles": [object(), object()]})()

    monkeypatch.setattr(fusion_dependencies, "_get_profile_source", ProfileSource)
    monkeypatch.setattr(
        "app.infra.indexing.profile_embedding_store.ProfileEmbeddingStore",
        lambda **kwargs: object(),
    )

    class PartialFailureIndexer:
        def __init__(self, **kwargs):
            return None

        def profiles_missing_fragments(self, profiles, indexed_ids):
            return profiles

        def build_index(self, **kwargs):
            return IndexingResult(
                total_profiles=2,
                indexed_count=1,
                failed_count=1,
            )

    monkeypatch.setattr(
        "app.domain.services.profile_embedding_indexer.ProfileEmbeddingIndexer",
        PartialFailureIndexer,
    )

    class RunImmediatelyThread:
        def __init__(self, *, target, daemon: bool, name: str) -> None:
            self.target = target
            assert daemon is True
            assert name == "bg-index-build"

        def start(self) -> None:
            # Synchronous test execution must provide the shutdown signal that
            # a real OS thread could observe before a retry is scheduled.
            worker = getattr(self.target, "__self__", None)
            stop_event = getattr(worker, "_stop_event", None)
            if stop_event is not None:
                stop_event.set()
            self.target()

        def join(self) -> None:
            return None

    monkeypatch.setattr(threading, "Thread", RunImmediatelyThread)

    status = IndexBackfillStatus()
    context = build_context(status)
    app = create_bcsfuse_app(context)

    with TestClient(app):
        snapshot = status.snapshot()

    assert snapshot["running"] is False
    assert snapshot["failed"] is True
    response = ready(app)
    assert response.status_code == 503
    assert response.json()["index_backfill_failed"] is True
