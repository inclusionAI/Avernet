"""Phase 6 — HTTP contract tests for `engine/api/default_config/router.py`."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.default_config.router import router as dc_router
from engine.community.core.default_config.models import DefaultConfigResult
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager


class _EngineWithDC(BaseEngine):
    name = "rich"
    version = "1.0.0"
    _CAPABILITIES = EngineCapabilities(
        supported={Capability.DEFAULT_CONFIG_GET},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


class _EngineWithoutDC(BaseEngine):
    name = "lean"
    version = "0.1.0"
    _CAPABILITIES = EngineCapabilities(
        supported={Capability.SESSION_LIST, Capability.CHAT_STREAM},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


def _install(engine_cls: type[BaseEngine]) -> EngineManager:
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(engine_cls)
    m = EngineManager(engine_cls.name, registry=registry)
    m._active_engine = engine_cls()
    EngineManager._instance = m
    return m


@pytest.fixture
def rich_manager():
    m = _install(_EngineWithDC)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def lean_manager():
    m = _install(_EngineWithoutDC)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(dc_router)
    return TestClient(app)


def test_dispatches(rich_manager, client):
    plugin = MagicMock()
    plugin.get_default_config = AsyncMock(
        return_value=DefaultConfigResult(
            path="/etc/openclaw.json", config={"foo": "bar"},
        )
    )
    rich_manager._active_engine._default_config = plugin

    resp = client.get("/api/openclaw/default-config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["path"] == "/etc/openclaw.json"
    assert body["data"]["config"] == {"foo": "bar"}


def test_404_when_missing(rich_manager, client):
    plugin = MagicMock()
    plugin.get_default_config = AsyncMock(
        side_effect=FileNotFoundError("missing"),
    )
    rich_manager._active_engine._default_config = plugin
    assert client.get("/api/openclaw/default-config").status_code == 404


def test_500_on_bad_json(rich_manager, client):
    plugin = MagicMock()
    plugin.get_default_config = AsyncMock(side_effect=ValueError("bad json"))
    rich_manager._active_engine._default_config = plugin
    assert client.get("/api/openclaw/default-config").status_code == 500


def test_501(lean_manager, client):
    assert client.get("/api/openclaw/default-config").status_code == 501
