"""HTTP tests for ``GET /api/engine/active-sessions``.

Covers the dual-axis response model on the route:
  - active engine exposing ``active_sessions`` → ok/clear | ok/active
  - active engine WITHOUT the surface → unsupported/unknown
  - query exception → error/unknown
  - read-only + non-blocking: the route degrades to ``unknown`` rather than 500,
    and existing ``/api/engine/status`` behaviour is unchanged.

Mirrors the ``test_engine_router.py`` fixture strategy (construct an
``EngineManager`` directly and poke it into the singleton).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.core.adapters.openclaw.active_run_registry import (
    ActiveSessionQueryResult,
)
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager
from engine.community.api.engine import router as engine_router


# ── engine fixtures ────────────────────────────────────────────────────────────


class _ActiveSessionsEngine(BaseEngine):
    """Engine that exposes ``active_sessions`` (OpenClaw-like)."""

    name = "with-active"
    version = "1.0.0"

    _CAPABILITIES = EngineCapabilities(
        supported={Capability.SESSION_LIST, Capability.CHAT_STREAM, Capability.SESSION_ACTIVE_QUERY}
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()
        self.active_sessions = AsyncMock()


class _PlainEngine(BaseEngine):
    """Engine with no ``active_sessions`` surface (unsupported path)."""

    name = "plain"
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
    r.register(_ActiveSessionsEngine)
    r.register(_PlainEngine)
    return r


@pytest.fixture
def manager(registry: EngineRegistry):
    EngineManager.reset_instance()
    m = EngineManager("with-active", registry=registry)
    m._active_engine = _ActiveSessionsEngine()
    EngineManager._instance = m
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client(manager: EngineManager) -> TestClient:
    app = FastAPI()
    app.include_router(engine_router)
    return TestClient(app)


def _result(verdict: str, count: int = 0, engine: str = "with-active") -> ActiveSessionQueryResult:
    from datetime import UTC, datetime

    return ActiveSessionQueryResult(
        query_status="ok",
        verdict=verdict,  # type: ignore[arg-type]
        engine=engine,
        checked_at=datetime.now(UTC),
        count=count,
        sessions=[],
    )


# ── endpoint ──────────────────────────────────────────────────────────────────


class TestActiveSessionsEndpoint:
    def test_clear_when_no_active_runs(self, client: TestClient, manager: EngineManager):
        manager._active_engine.active_sessions = AsyncMock(return_value=_result("clear"))
        resp = client.get("/api/engine/active-sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query_status"] == "ok"
        assert body["verdict"] == "clear"
        assert body["engine"] == "with-active"
        assert body["count"] == 0
        assert body["sessions"] == []
        assert "checked_at" in body

    def test_active_when_runs_in_flight(self, client: TestClient, manager: EngineManager):
        manager._active_engine.active_sessions = AsyncMock(
            return_value=ActiveSessionQueryResult(
                query_status="ok",
                verdict="active",
                engine="with-active",
                checked_at="2026-09-01T12:00:00Z",
                count=1,
                sessions=[
                    {
                        "session_id": "session:a:user:u",
                        "run_id": "run-1",
                        "state": "running",
                        "started_at": "2026-09-01T12:00:00Z",
                        "updated_at": "2026-09-01T12:00:01Z",
                        "agent_id": None,
                    }
                ],
            )
        )
        resp = client.get("/api/engine/active-sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "active"
        assert body["count"] == 1
        assert body["sessions"][0]["run_id"] == "run-1"

    def test_unsupported_when_engine_lacks_surface(self, client: TestClient, manager: EngineManager):
        # Swap to an engine without active_sessions.
        manager._active_engine = _PlainEngine()
        resp = client.get("/api/engine/active-sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query_status"] == "unsupported"
        assert body["verdict"] == "unknown"

    def test_error_when_engine_raises(self, client: TestClient, manager: EngineManager):
        manager._active_engine.active_sessions = AsyncMock(side_effect=RuntimeError("boom"))
        resp = client.get("/api/engine/active-sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query_status"] == "error"
        assert body["verdict"] == "unknown"

    def test_forwards_query_params(self, client: TestClient, manager: EngineManager):
        manager._active_engine.active_sessions = AsyncMock(return_value=_result("clear"))
        client.get("/api/engine/active-sessions?session_id=session:a:user:u&agent_id=agent-7&timeout_seconds=0.5")
        manager._active_engine.active_sessions.assert_awaited_once()
        kwargs = manager._active_engine.active_sessions.await_args.kwargs
        assert kwargs == {"session_id": "session:a:user:u", "agent_id": "agent-7", "timeout": 0.5}

    def test_existing_status_endpoint_unchanged(self, client: TestClient, manager: EngineManager):
        resp = client.get("/api/engine/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["engine"] == "with-active"
        assert "active_connections" in body