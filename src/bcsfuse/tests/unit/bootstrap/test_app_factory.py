from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.bootstrap.app_factory import create_bcsfuse_app
from src.bootstrap.application_context import ApplicationContext
from src.bootstrap.provider_registry import ProviderRegistry


@pytest.fixture(autouse=True)
def disable_background_index(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    from src.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags.reset()
    yield
    FeatureFlags.reset()


class StartupSpy:
    def __init__(self, *, fail_on_initialize: bool = False) -> None:
        self.fail_on_initialize = fail_on_initialize
        self.initialized = False
        self.shutdown_called = False

    async def initialize(self) -> None:
        if self.fail_on_initialize:
            raise RuntimeError("startup failed")
        self.initialized = True

    async def shutdown(self) -> None:
        self.shutdown_called = True


class FailingKeysRegistry(ProviderRegistry):
    def keys(self) -> list[str]:
        raise RuntimeError("secret-value-must-not-leak")


class FailingVectorStoreRegistry(ProviderRegistry):
    def get(self, name: str):
        if name == "vector_store":
            raise RuntimeError("secret-value-must-not-leak")
        return super().get(name)


def build_context(
    *,
    registry: ProviderRegistry | None = None,
    startup: StartupSpy | None = None,
) -> tuple[ApplicationContext, StartupSpy]:
    provider_registry = registry or ProviderRegistry()
    startup_provider = startup or StartupSpy()
    provider_registry.register("config", object())
    provider_registry.register("secret_provider", object())
    provider_registry.register("startup_provider", startup_provider)
    context = ApplicationContext(
        mode="test",
        startup_profile="opensource",
        registry=provider_registry,
    )
    return context, startup_provider


def test_factory_uses_supplied_context() -> None:
    context, _ = build_context()

    app = create_bcsfuse_app(context)

    assert app.state.context is context
    assert app.title == "BCSFuse"
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "startup_profile": "opensource",
        "provider_mode": "test",
        "process_health": "alive",
    }


@pytest.mark.parametrize("missing", ["config", "secret_provider", "startup_provider"])
def test_factory_rejects_missing_required_provider(missing: str) -> None:
    context, _ = build_context()
    context.registry.unregister(missing)

    with pytest.raises(LookupError, match=missing):
        create_bcsfuse_app(context)


def test_provider_registry_require_returns_registered_falsey_value() -> None:
    registry = ProviderRegistry()
    registry.register("falsey", 0)

    assert registry.require("falsey") == 0


def test_provider_registry_require_rejects_missing_name() -> None:
    registry = ProviderRegistry()

    with pytest.raises(LookupError, match="missing"):
        registry.require("missing")


def test_factory_runs_startup_and_shutdown_lifecycle() -> None:
    context, startup = build_context()
    app = create_bcsfuse_app(context)

    with TestClient(app) as client:
        assert startup.initialized is True
        assert client.get("/ready").status_code == 200

    assert startup.shutdown_called is True


def test_factory_propagates_startup_failure() -> None:
    context, startup = build_context(startup=StartupSpy(fail_on_initialize=True))
    app = create_bcsfuse_app(context)

    with pytest.raises(RuntimeError, match="startup failed"):
        with TestClient(app):
            pass

    assert startup.shutdown_called is False


def test_factory_propagates_business_route_mount_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    context, _ = build_context()

    def fail_to_mount_routes(_: object) -> None:
        raise RuntimeError("route mount failed")

    monkeypatch.setattr(
        "src.bootstrap.app_factory.include_oss_business_routes",
        fail_to_mount_routes,
    )

    with pytest.raises(RuntimeError, match="route mount failed"):
        create_bcsfuse_app(context)


def test_readiness_redacts_registry_failure_details() -> None:
    context, _ = build_context(registry=FailingKeysRegistry())
    app = create_bcsfuse_app(context)

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "provider_mode": "test",
        "error": "Provider registry initialization failed: RuntimeError",
        "providers": 0,
    }
    assert "secret-value-must-not-leak" not in response.text


def test_readiness_redacts_vector_store_failure_details() -> None:
    context, _ = build_context(registry=FailingVectorStoreRegistry())
    app = create_bcsfuse_app(context)

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["vector_store_error"] == "RuntimeError"
    assert "secret-value-must-not-leak" not in response.text
