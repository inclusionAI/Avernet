"""Tests for M4 — the /api/engine/* router and the capability guard helper.

Covers:
- `EngineManager.get_capabilities()` / `get_registered_engines()` (unit)
- `/api/engine/status|capabilities|list|restart` (HTTP contract), plus the
  absence of the retired `/api/engine/switch`
- `engine.community.api.caps.check_capability` (supported/limited/unsupported branches)

The tests bypass the singleton by constructing an EngineManager directly and
poking it into `EngineManager._instance`, so no global state from other tests
or the bundled OpenClaw registry leaks in.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager
from engine.community.api.caps import check_capability
from engine.community.api.engine import router as engine_router


# ─────────────────────────────────────────────────────────────────────────────
# Engine fixtures
# ─────────────────────────────────────────────────────────────────────────────


class _RichEngine(BaseEngine):
    """Engine declaring a representative mix of supported/limited caps."""

    name = "rich"
    version = "1.2.3"

    _CAPABILITIES = EngineCapabilities(
        supported={
            Capability.SESSION_LIST,
            Capability.SESSION_CREATE,
            Capability.CHAT_STREAM,
            Capability.CRON_LIST,
        },
        limited={
            Capability.MCP_START: "use mcporter CLI",
        },
        fallback={
            Capability.SKILLS_EXECUTE: "not available on this engine",
        },
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()
        self._cron = MagicMock()


class _PoorEngine(BaseEngine):
    """Engine declaring only the mandatory surface — good for 'not active' tests."""

    name = "poor"
    version = "0.1.0"

    _CAPABILITIES = EngineCapabilities(
        supported={Capability.SESSION_LIST, Capability.CHAT_STREAM}
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()


@pytest.fixture
def registry() -> EngineRegistry:
    r = EngineRegistry()
    r.register(_RichEngine)
    r.register(_PoorEngine)
    return r


@pytest.fixture
def manager(registry: EngineRegistry, monkeypatch):
    """Construct a manager with `rich` active and install it as the singleton.

    `check_capability` and the router handlers look up `EngineManager.get_instance()`,
    so the test swap here routes them to our controlled manager.
    """
    EngineManager.reset_instance()
    m = EngineManager("rich", registry=registry)
    m._active_engine = _RichEngine()
    EngineManager._instance = m
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client(manager: EngineManager) -> TestClient:
    """TestClient over just the engine router — keeps other routers out of the picture."""
    app = FastAPI()
    app.include_router(engine_router)
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# EngineManager.get_capabilities / get_registered_engines
# ─────────────────────────────────────────────────────────────────────────────


class TestGetCapabilities:
    def test_returns_active_engine_caps_when_engine_is_none(
        self, manager: EngineManager
    ):
        caps = manager.get_capabilities()
        assert Capability.CHAT_STREAM in caps.supported
        assert caps.limited[Capability.MCP_START] == "use mcporter CLI"

    def test_returns_active_engine_caps_when_engine_matches(
        self, manager: EngineManager
    ):
        caps = manager.get_capabilities("rich")
        assert Capability.CHAT_STREAM in caps.supported

    def test_returns_other_engine_caps_via_class_attribute(
        self, manager: EngineManager
    ):
        # `poor` isn't active — pulls from the registry without activating it.
        caps = manager.get_capabilities("poor")
        assert caps.supported == {Capability.SESSION_LIST, Capability.CHAT_STREAM}
        assert not caps.limited

    def test_raises_when_engine_not_registered(self, manager: EngineManager):
        with pytest.raises(Exception):  # EngineNotFoundError
            manager.get_capabilities("does-not-exist")


class TestGetRegisteredEngines:
    def test_lists_all_with_active_flag(self, manager: EngineManager):
        engines = manager.get_registered_engines()
        by_name = {e["name"]: e for e in engines}
        assert set(by_name) == {"rich", "poor"}
        assert by_name["rich"]["active"] is True
        assert by_name["poor"]["active"] is False
        assert by_name["rich"]["version"] == "1.2.3"
        assert by_name["poor"]["version"] == "0.1.0"


# ─────────────────────────────────────────────────────────────────────────────
# check_capability guard helper
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckCapability:
    def test_supported_returns_none(self, manager: EngineManager):
        assert check_capability(Capability.SESSION_LIST) is None

    def test_limited_returns_warning_string(self, manager: EngineManager):
        assert check_capability(Capability.MCP_START) == "use mcporter CLI"

    def test_fallback_raises_501_with_fallback_detail(
        self, manager: EngineManager
    ):
        with pytest.raises(HTTPException) as exc:
            check_capability(Capability.SKILLS_EXECUTE)
        assert exc.value.status_code == 501
        assert exc.value.detail == "not available on this engine"

    def test_fully_unsupported_raises_501_with_generic_detail(
        self, manager: EngineManager
    ):
        # SESSION_DELETE isn't declared at all on _RichEngine.
        with pytest.raises(HTTPException) as exc:
            check_capability(Capability.SESSION_DELETE)
        assert exc.value.status_code == 501
        assert "session.delete" in exc.value.detail
        assert "rich" in exc.value.detail


# ─────────────────────────────────────────────────────────────────────────────
# HTTP — /api/engine/*
# ─────────────────────────────────────────────────────────────────────────────


class TestEngineCapabilitiesEndpoint:
    def test_active_engine_caps(self, client: TestClient):
        resp = client.get("/api/engine/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["engine"] == "rich"
        assert "chat.stream" in body["data"]["supported"]
        assert body["data"]["limited"]["mcp.start"] == "use mcporter CLI"

    def test_named_engine_caps(self, client: TestClient):
        resp = client.get("/api/engine/capabilities?engine=poor")
        assert resp.status_code == 200
        body = resp.json()
        assert body["engine"] == "poor"
        assert set(body["data"]["supported"]) == {"session.list", "chat.stream"}

    def test_unknown_engine_returns_404(self, client: TestClient):
        resp = client.get("/api/engine/capabilities?engine=nope")
        assert resp.status_code == 404
        assert resp.json()["success"] is False


class TestEngineListEndpoint:
    def test_list_contains_all_registered_with_active_flag(
        self, client: TestClient
    ):
        resp = client.get("/api/engine/list")
        assert resp.status_code == 200
        engines = resp.json()["data"]["engines"]
        by_name = {e["name"]: e for e in engines}
        assert by_name["rich"]["active"] is True
        assert by_name["poor"]["active"] is False


class TestEngineStatusEndpoint:
    def test_status_returns_engine_shape(
        self, client: TestClient, manager: EngineManager
    ):
        resp = client.get("/api/engine/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["engine"] == "rich"
        assert "active_connections" in body
        assert "process" in body


class TestEngineSwitchEndpointIsRetired:
    """`POST /api/engine/switch` was removed — inclusionAI/Avernet#914.

    A bot's engine is fixed at creation, so there is no runtime engine swap to
    expose. The route must stay gone: consumers derive a bot's runtime from its
    record, and a live swap would make that derivation describe the wrong
    process.
    """

    def test_switch_route_is_not_registered(self, client: TestClient):
        resp = client.post("/api/engine/switch", json={"engine": "poor"})
        assert resp.status_code == 404


class TestEngineRestartEndpoint:
    def test_restart_ok(
        self, client: TestClient, manager: EngineManager, monkeypatch
    ):
        monkeypatch.setattr(
            manager,
            "restart",
            AsyncMock(return_value={"restarted": True, "engine": "rich"}),
        )
        resp = client.post("/api/engine/restart", json={"force": False})
        assert resp.status_code == 200
        assert resp.json()["restarted"] is True

    def test_restart_conflict_returns_409(
        self, client: TestClient, manager: EngineManager, monkeypatch
    ):
        async def _raise_conflict(force=False):
            raise RuntimeError("active connections block restart")

        monkeypatch.setattr(manager, "restart", _raise_conflict)
        resp = client.post("/api/engine/restart", json={"force": False})
        assert resp.status_code == 409
