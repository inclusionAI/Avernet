"""Tests for the GET /api/engine/active-sessions route.

Configs:
- Active engine exposes `query_active_sessions` (OpenClawEngine-like) → routes
  to it and reflects the response envelope.
- Active engine doesn't support it → `query_status=unsupported, verdict=unknown`.
- `_active_engine is None` (uninitialized manager) → unsupported / unknown.
- Engine's `query_active_sessions` raises → `error / unknown`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.engine import router as engine_router
from engine.community.manager import EngineManager


class _NoSupportActiveEngine:
    """Engine that does NOT expose query_active_sessions (representative of
    a non-OpenClaw active engine today)."""

    name = "noquery"

    def __init__(self) -> None:
        self._active_run_registry = None


class _QueryActiveEngine:
    """Stand-in for OpenClawEngine at the route level — only exposes
    `query_active_sessions` so the route can decide to delegate."""

    name = "openclaw"

    def __init__(self, *, result: dict | None = None, raises: BaseException | None = None) -> None:
        self._result = result or {"query_status": "ok", "verdict": "clear", "engine": "openclaw", "checked_at": "now", "active_session_count": 0, "sessions": []}
        self._raises = raises
        self.calls: list[int] = []

    async def query_active_sessions(self, *, timeout_ms: int | None = None) -> dict:
        self.calls.append(timeout_ms)
        if self._raises is not None:
            raise self._raises
        return dict(self._result)


@pytest.fixture
def manager(monkeypatch):
    """Custom manager fixture — uses an instance attribute (not the singleton)
    so test isolation is preserved."""
    EngineManager.reset_instance()
    m = MagicMock(spec=EngineManager)
    # `name` attribute on spec instances is treated as a class attribute of the
    # spec'd class; set the public method explicitly.
    m.active_engine_instance = MagicMock()
    m.engine = "openclaw"
    monkeypatch.setattr(EngineManager, "get_instance", classmethod(lambda cls: m))
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client(manager) -> TestClient:
    app = FastAPI()
    app.include_router(engine_router)
    return TestClient(app)


class TestUnsupported:
    def test_non_openclaw_active_engine(self, manager, client):
        manager.engine = "not-openclaw"
        manager.active_engine_instance.return_value = _NoSupportActiveEngine()
        resp = client.get("/api/engine/active-sessions")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["query_status"] == "unsupported"
        assert data["verdict"] == "unknown"
        assert data["engine"] == "not-openclaw"
        assert data["active_session_count"] == 0
        assert data["sessions"] == []
        assert data.get("incomplete") is True

    def test_manager_not_initialized(self, manager, client):
        manager.active_engine_instance.return_value = None
        manager.engine = "openclaw"
        resp = client.get("/api/engine/active-sessions")
        data = resp.json()["data"]
        assert data["query_status"] == "unsupported"
        assert data["verdict"] == "unknown"
        assert data["engine"] == "openclaw"


class TestDelegates:
    def test_clear_response_propagates(self, manager, client):
        eng = _QueryActiveEngine(result={
            "query_status": "ok", "verdict": "clear", "engine": "openclaw",
            "checked_at": "2025-01-01T00:00:00+00:00",
            "active_session_count": 0, "sessions": [],
        })
        manager.active_engine_instance.return_value = eng
        resp = client.get("/api/engine/active-sessions?timeout_ms=500")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["query_status"] == "ok"
        assert data["verdict"] == "clear"
        assert data["active_session_count"] == 0
        assert data["sessions"] == []
        assert eng.calls == [500]

    def test_active_response_propagates_sessions(self, manager, client):
        result = {
            "query_status": "ok", "verdict": "active", "engine": "openclaw",
            "checked_at": "2025-01-01T00:00:00+00:00",
            "active_session_count": 1,
            "sessions": [{
                "session_id": "abc", "run_id": "r1", "agent_id": None,
                "started_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-01T00:01:00+00:00",
                "last_state": "running", "last_event_at": "2025-01-01T00:01:00+00:00",
            }],
        }
        eng = _QueryActiveEngine(result=result)
        manager.active_engine_instance.return_value = eng
        resp = client.get("/api/engine/active-sessions")
        data = resp.json()["data"]
        assert data["verdict"] == "active"
        assert data["active_session_count"] == 1
        assert data["sessions"][0]["session_id"] == "abc"

    def test_timeout_response_propagates(self, manager, client):
        eng = _QueryActiveEngine(result={
            "query_status": "timeout", "verdict": "unknown", "engine": "openclaw",
            "checked_at": "2025-01-01T00:00:00+00:00",
            "active_session_count": 0, "sessions": [],
            "error_message": "active-sessions query exceeded timeout",
        })
        manager.active_engine_instance.return_value = eng
        resp = client.get("/api/engine/active-sessions?timeout_ms=1")
        data = resp.json()["data"]
        assert data["query_status"] == "timeout"
        assert data["verdict"] == "unknown"

    def test_error_response_propagates(self, manager, client):
        eng = _QueryActiveEngine(result={
            "query_status": "error", "verdict": "unknown", "engine": "openclaw",
            "checked_at": "2025-01-01T00:00:00+00:00",
            "active_session_count": 0, "sessions": [], "incomplete": True,
            "error_message": "boom",
        })
        manager.active_engine_instance.return_value = eng
        resp = client.get("/api/engine/active-sessions")
        data = resp.json()["data"]
        assert data["query_status"] == "error"
        assert data["verdict"] == "unknown"
        assert data.get("incomplete") is True

    def test_engine_raises_yields_error_unknown(self, manager, client):
        eng = _QueryActiveEngine(raises=RuntimeError("boom"))
        manager.active_engine_instance.return_value = eng
        # Production engines should never raise from query_active_sessions,
        # but the route is defensive: any uncaught exception must produce an
        # HTTP 200 envelope with query_status=error / verdict=unknown.
        resp = client.get("/api/engine/active-sessions")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["query_status"] == "error"
        assert data["verdict"] == "unknown"
        assert data.get("incomplete") is True
        assert data["engine"] == "openclaw"


class TestCompatRegression:
    """The endpoint must NOT change existing /api/engine/status behaviour."""

    def test_status_route_still_works(self, manager, client):
        manager.status = AsyncMock(return_value={"engine": "openclaw"})
        # AsyncMock.__call__ returns a coroutine; FastAPI awaits it.
        resp = client.get("/api/engine/status")
        assert resp.status_code == 200 and resp.json()["engine"] == "openclaw"
