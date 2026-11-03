"""TDD contract for automatic recovery after a failed initial rebuild."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.index_backfill_status import IndexBackfillStatus
from app.bootstrap.provider_registry import ProviderRegistry
from app.providers.public.vector_index_startup_provider import (
    VectorIndexStartupProvider,
)


@pytest.fixture(autouse=True)
def _disable_profile_index(monkeypatch: pytest.MonkeyPatch):
    from app.infra.config.feature_flags import FeatureFlags

    FeatureFlags.reset()
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags._get_settings()
    yield
    FeatureFlags.reset()


class NoopDelegate:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class ReadOnlyStartupStore:
    """An active durable owner exists; this replica must only synchronize."""

    def __init__(self) -> None:
        self.read_only = True
        self.events: list[str] = []

    def synchronize_read_view(self) -> dict[str, object]:
        self.events.append("sync")
        return {"read_only": True, "synchronized_version": 49}

    def rebuild_from_backend(self) -> dict[str, int]:  # pragma: no cover
        self.events.append("rebuild")
        raise AssertionError("read-only startup must not rebuild")

    def close(self) -> None:
        self.events.append("close")


class FailingThenRecoveredStore:
    def __init__(self) -> None:
        self.rebuild_count = 0

    def rebuild_from_backend(self) -> dict[str, int]:
        self.rebuild_count += 1
        if self.rebuild_count == 1:
            raise RuntimeError("qdrant unavailable")
        return {"loaded_count": 2}

    def index_dependency_readiness(self) -> dict[str, bool]:
        return {
            "dense_qdrant_ready": self.rebuild_count > 1,
            "keyword_qdrant_ready": self.rebuild_count > 1,
        }

    def size(self) -> int:
        return 2


@pytest.mark.asyncio
async def test_startup_rebuild_failure_is_cleared_by_retry() -> None:
    store = FailingThenRecoveredStore()
    provider = VectorIndexStartupProvider(
        NoopDelegate(),
        store,
        recovery_interval_seconds=0.01,
    )

    await provider.initialize()

    assert store.startup_rebuild_failed is True
    assert store.startup_rebuild_error_type == "RuntimeError"

    deadline = time.monotonic() + 1.0
    while not getattr(store, "startup_rebuild_failed", True):
        break
    while time.monotonic() < deadline:
        if not getattr(store, "startup_rebuild_failed", True):
            break
        await asyncio.sleep(0.01)

    await provider.shutdown()

    assert store.rebuild_count == 2
    assert store.startup_rebuild_failed is False
    assert store.startup_rebuild_error_type is None


@pytest.mark.asyncio
async def test_active_owner_replica_synchronizes_without_rebuild() -> None:
    store = ReadOnlyStartupStore()
    provider = VectorIndexStartupProvider(
        NoopDelegate(), store, recovery_interval_seconds=0.01
    )

    await provider.initialize()

    assert store.events == ["sync"]
    assert getattr(store, "startup_rebuild_failed", False) is False

    await provider.shutdown()

    assert store.events == ["sync", "close"]


def _recovery_context(store: Any) -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register(
        "startup_provider",
        VectorIndexStartupProvider(
            NoopDelegate(),
            store,
            recovery_interval_seconds=0.01,
        ),
    )
    registry.register("vector_store", store)
    return ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )


def test_readiness_stops_staying_unready_after_dependencies_recover() -> None:
    store = FailingThenRecoveredStore()
    app = create_bcsfuse_app(_recovery_context(store))

    with TestClient(app) as client:
        first = client.get("/ready")
        assert first.status_code == 503
        body = first.json()
        assert body["startup_rebuild_failed"] is True

        deadline = time.monotonic() + 2.0
        recovered = False
        while time.monotonic() < deadline:
            response = client.get("/ready")
            if response.status_code == 200:
                recovered = True
                body = response.json()
                break
            time.sleep(0.02)

        assert recovered is True
        assert body["startup_rebuild_failed"] is False
        assert body["startup_rebuild_error_type"] is None


def test_failed_background_backfill_retries_to_completed() -> None:
    from app.bootstrap.index_backfill_worker import IndexBackfillWorker

    calls: list[int] = []
    stop = threading.Event()
    status = IndexBackfillStatus()

    def target() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("qdrant unavailable")

    worker = IndexBackfillWorker(
        target=target,
        status=status,
        stop_event=stop,
        retry_interval_seconds=0.01,
    )
    worker.run()

    assert status.snapshot()["failed"] is False
    assert status.snapshot()["running"] is False
    assert len(calls) == 2
