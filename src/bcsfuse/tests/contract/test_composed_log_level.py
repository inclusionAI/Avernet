"""Runtime log levels must work with externally owned logging handlers."""

import io
import logging

import pytest
from fastapi.testclient import TestClient

from src.bootstrap.app_factory import create_bcsfuse_app
from src.bootstrap.application_context import ApplicationContext
from src.bootstrap.provider_registry import ProviderRegistry
from src.infra.trace_context import get_trace_id


@pytest.fixture(autouse=True)
def logging_state(monkeypatch):
    from src.infra.config.feature_flags import FeatureFlags

    for key in ("LOG_LEVEL", "SERVER_ENV", "REAL_SERVER_ENV", "ALIPAY_APP_ENV"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags.reset()
    root = logging.getLogger()
    business = logging.getLogger("src")
    handlers, level, business_level = root.handlers[:], root.level, business.level
    factory = logging.getLogRecordFactory()
    root.handlers = []
    root.setLevel(logging.WARNING)
    business.setLevel(logging.NOTSET)
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers = handlers
    root.setLevel(level)
    business.setLevel(business_level)
    logging.setLogRecordFactory(factory)
    FeatureFlags.reset()


class HostStartup:
    async def initialize(self):
        # Host initialization can reset the application logger's level.
        logging.getLogger("src").setLevel(logging.WARNING)

    async def shutdown(self):
        pass


def make_app():
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", HostStartup())
    app = create_bcsfuse_app(ApplicationContext(
        mode="test", startup_profile="test-host", registry=registry,
    ))

    @app.get("/log-probe")
    async def probe():
        logger = logging.getLogger("src.application.services.level_probe")
        logger.debug("candidate-detail")
        logger.info("pipeline-summary")
        logging.getLogger("httpx").debug("third-party-detail")
        return {"trace": get_trace_id()}

    return app


@pytest.mark.parametrize(
    "environment,override,expected_debug",
    [
        ("prepub", None, True),
        ("pre", None, True),
        ("prod", None, False),
        ("", None, False),
        ("prepub", "INFO", False),
        ("prod", " debug ", True),
    ],
)
def test_composed_runtime_level_and_trace_preserve_host_output(
    monkeypatch, environment, override, expected_debug,
):
    monkeypatch.setenv("SERVER_ENV", environment)
    if override is not None:
        monkeypatch.setenv("LOG_LEVEL", override)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    formatter = logging.Formatter("host [%(traceid)s] %(message)s")
    handler.setFormatter(formatter)
    errors = logging.StreamHandler(io.StringIO())
    errors.setLevel(logging.ERROR)
    root = logging.getLogger()
    root.handlers = [handler, errors]
    app = make_app()
    with TestClient(app) as client:
        response = client.get("/log-probe", headers={"X-Trace-ID": "level-probe"})
    assert response.json() == {"trace": "level-probe"}
    emitted = output.getvalue()
    assert "host [level-probe] pipeline-summary" in emitted
    assert ("host [level-probe] candidate-detail" in emitted) is expected_debug
    assert "third-party-detail" not in emitted
    assert f"business_log_level={'DEBUG' if expected_debug else 'INFO'}" in emitted
    assert root.handlers == [handler, errors]
    assert handler.formatter is formatter
    assert errors.level == logging.ERROR
    assert root.level == logging.WARNING


@pytest.mark.parametrize("fallback", ["REAL_SERVER_ENV", "ALIPAY_APP_ENV"])
def test_prepub_environment_fallback_enables_debug(monkeypatch, fallback):
    monkeypatch.setenv(fallback, "prepub")
    with TestClient(make_app()):
        assert logging.getLogger("src.application.services.new_logger").isEnabledFor(logging.DEBUG)


def test_server_environment_wins_over_fallback(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "prod")
    monkeypatch.setenv("REAL_SERVER_ENV", "prepub")
    with TestClient(make_app()):
        assert not logging.getLogger("src.application.services.new_logger").isEnabledFor(logging.DEBUG)


def test_invalid_level_rejected_before_startup(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "invalid")
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        make_app()


def test_public_startup_without_host_handlers_emits_trace(monkeypatch, capsys):
    monkeypatch.setenv("SERVER_ENV", "prepub")
    # Pytest attaches its capture handlers after fixture setup, unlike a bare host.
    logging.getLogger().handlers = []
    with TestClient(make_app()) as client:
        client.get("/log-probe", headers={"X-Trace-ID": "public-probe"})
    captured = capsys.readouterr()
    assert "[public-probe]" in captured.err
    assert "candidate-detail" in captured.err
