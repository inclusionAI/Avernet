"""TC lifecycle owns storage after host initialization and closes its index."""

import pytest
from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.opensource import build_opensource_provider_registry
from fastapi.testclient import TestClient


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


@pytest.mark.parametrize("source", ["environment", "host"])
def test_tc_cors_accepts_configured_origin_for_legacy_config(source, monkeypatch):
    origin = "https://teamclaw.example.test"
    context, _ = build_context()
    if source == "environment":
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", r"https://teamclaw\.example\.test")
    else:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        get = context.config.get

        def host_get(key, default=None):
            return [origin] if key == "tc.cors_allowed_origins" else get(key, default)

        monkeypatch.setattr(context.config, "get", host_get)

    client = TestClient(create_bcsfuse_app(context))
    path = "/v1/workers/test-bot/config"
    preflight = {
        "Origin": origin,
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type",
    }
    allowed = client.options(path, headers=preflight)
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == origin
    assert allowed.headers["access-control-allow-credentials"] == "true"

    rejected = client.options(path, headers={**preflight, "Origin": origin + ".evil"})
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers
    assert (
        client.get("/health", headers={"Origin": origin}).headers[
            "access-control-allow-origin"
        ]
        == origin
    )


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
    from types import SimpleNamespace

    from app.tc import bootstrap

    context, startup = build_context()
    closed = []

    class Index:
        def close(self):
            closed.append(True)

    def no_database_access():
        raise AssertionError("composition must not connect to the build store")

    repository = SimpleNamespace(
        connect=no_database_access,
        upsert=no_database_access,
        environment="test",
        placeholder="?",
    )
    monkeypatch.setattr(bootstrap, "storage", lambda context: (repository, Index()))

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
