"""The shared L2 reader must have an explicit process lifecycle."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
import pytest


@pytest.fixture(autouse=True)
def disable_background_index(monkeypatch):
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


class SharedResponseMemoryStore:
    def get(self, key: str, *, index_version: int) -> bytes | None:
        return None

    def store(self, key: str, payload: bytes, *, index_version: int, ttl_seconds: float) -> bool:
        return True


def build_app() -> object:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("shared_response_cache", SharedResponseMemoryStore())
    context = ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )
    return create_bcsfuse_app(context)


def test_shared_l2_reader_is_closed_on_application_shutdown(monkeypatch) -> None:
    from app.adapters.http import shared_response_cache_reader as module

    closed: list[object] = []

    async def fail_if_not_closed(self) -> None:
        closed.append(self)

    monkeypatch.setattr(
        module.BoundedSharedResponseCacheReader,
        "close",
        fail_if_not_closed,
    )

    app = build_app()
    with TestClient(app):
        assert app.state.shared_cache_reader is not None

    assert len(closed) == 1
    assert closed[0] is app.state.shared_cache_reader


def test_real_reader_closes_its_bounded_executor() -> None:
    import asyncio

    from app.adapters.http.shared_response_cache_reader import (
        BoundedSharedResponseCacheReader,
    )

    reader = BoundedSharedResponseCacheReader(
        shared_response_cache=SharedResponseMemoryStore(),
        timeout_seconds=0.01,
        max_concurrent_reads=1,
    )
    try:
        assert reader._executor._shutdown is False
        asyncio.run(reader.close())
        assert reader._executor._shutdown is True
    finally:
        if not reader._executor._shutdown:
            reader._executor.shutdown(wait=True, cancel_futures=True)
