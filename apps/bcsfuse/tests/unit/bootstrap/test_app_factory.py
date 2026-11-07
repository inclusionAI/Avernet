"""TC lifecycle owns storage after host initialization and closes its index."""

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.opensource import build_opensource_provider_registry


class StartupSpy:
    def __init__(self, *, fail_on_initialize=False):
        self.fail_on_initialize = fail_on_initialize
        self.initialized = False
        self.shutdown_called = False

    async def initialize(self):
        if self.fail_on_initialize:
            raise RuntimeError("startup failed")
        self.initialized = True

    async def shutdown(self):
        self.shutdown_called = True


@pytest.fixture(autouse=True)
def card_backend(monkeypatch):
    monkeypatch.setenv("BCSFUSE_TC_V1_STORAGE", "card")


def build_context(*, startup=None):
    registry = build_opensource_provider_registry("test")
    startup = startup or StartupSpy()
    registry.register("startup_provider", startup)
    return ApplicationContext(
        mode="test", startup_profile="opensource", registry=registry
    ), startup


def test_factory_is_lazy_and_uses_supplied_context():
    context, startup = build_context()
    app = create_bcsfuse_app(context)
    assert app.state.context is context
    assert not startup.initialized
    assert context.registry.get("tc_catalog_repository") is None
    assert TestClient(app).get("/health").json() == {
        "status": "ok",
        "startup_profile": "opensource",
        "provider_mode": "test",
        "process_health": "alive",
    }


@pytest.mark.parametrize("missing", ["config", "secret_provider", "startup_provider"])
def test_missing_required_provider_fails_composition(missing):
    context, _ = build_context()
    context.registry.unregister(missing)
    with pytest.raises(LookupError, match=missing):
        create_bcsfuse_app(context)


def test_factory_starts_card_updates_and_closes_index(monkeypatch):
    context, startup = build_context()
    with TestClient(create_bcsfuse_app(context)) as client:
        assert startup.initialized
        runtime = context.registry.require("tc_search")
        assert client.get("/ready").status_code == 200
        closed = []
        original = runtime.index.close

        def close():
            assert not startup.shutdown_called
            closed.append(True)
            original()

        monkeypatch.setattr(runtime.index, "close", close)
    assert closed == [True]
    assert startup.shutdown_called


def test_storage_failure_shuts_down_initialized_host():
    context, startup = build_context()

    def fail():
        assert startup.initialized
        raise RuntimeError("storage unavailable")

    context.registry.register("tc_storage_factory", fail)
    with (
        pytest.raises(RuntimeError, match="storage unavailable"),
        TestClient(create_bcsfuse_app(context)),
    ):
        pass
    assert startup.shutdown_called


def test_composition_failure_closes_open_index_and_host(monkeypatch):
    from app.tc import bootstrap

    context, startup = build_context()
    closed = []

    class Index:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(bootstrap, "storage", lambda context: (object(), Index()))

    def fail(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(bootstrap, "TcSearch", fail)
    with (
        pytest.raises(RuntimeError, match="model unavailable"),
        TestClient(create_bcsfuse_app(context)),
    ):
        pass
    assert closed == [True]
    assert startup.shutdown_called


def test_startup_failure_is_propagated():
    context, startup = build_context(startup=StartupSpy(fail_on_initialize=True))
    with (
        pytest.raises(RuntimeError, match="startup failed"),
        TestClient(create_bcsfuse_app(context)),
    ):
        pass
    assert not startup.shutdown_called


def test_readiness_reflects_current_index_state():
    context, _ = build_context()
    with TestClient(create_bcsfuse_app(context)) as client:

        class Pending:
            ready = False

        class PendingSearch:
            updates = Pending()

        context.registry.register("tc_search", PendingSearch())
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False
        assert response.json()["error"] == "INDEX_NOT_READY"
