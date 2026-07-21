"""Smoke tests for the community gateway app and plugin wiring."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.community.config import ConfigLoader
from gateway.community.logger import get_logger
from gateway.community.servers.web.app import app, create_app
from gateway.community.tracer import get_tracer_plugin


def test_pytest_works() -> None:
    """验证单测配置是否生效。"""
    assert True


def test_config_defaults_when_no_file() -> None:
    """ConfigLoader returns sane defaults when no config file is present."""
    config = ConfigLoader.load()
    assert config.app_name == "gateway"
    assert config.workers == 1
    assert config.module_config.web is not None
    assert config.module_config.web.port == 8888


def test_app_endpoints() -> None:
    """The community app exposes /api/test and /health."""
    client = TestClient(app)
    resp = client.get("/api/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "hello" in body["message"]

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"


def test_app_is_recreatable() -> None:
    """create_app builds a fresh app instance each call."""
    other = create_app()
    assert other is not app


def test_logger_returns_standard_logger() -> None:
    import logging

    log = get_logger("test-logger")
    assert isinstance(log, logging.Logger)


def test_tracer_plugin_has_trace_id() -> None:
    tracer = get_tracer_plugin()
    # Without an active span the bare tracer returns "-".
    assert tracer.get_trace_id() == "-"
