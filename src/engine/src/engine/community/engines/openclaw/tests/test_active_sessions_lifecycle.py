"""Lifecycle test: GET /api/engine/active-sessions verdict transitions.

Drives the active-sessions query through the full chain — the real
``OpenClawEngine``, the real ``_ChatPortMixin.chat_stream`` (via a fake
gateway client), and the real engine router mounted on a fresh FastAPI app —
so the verdict transitions ``clear`` → ``active`` → ``clear`` are pinned at
the integration level on a non-production OpenClaw fixture.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.engine import router as engine_router
from engine.community.engines.openclaw.engine import OpenClawEngine
from engine.community.manager import EngineManager
from engine.community.plugins.openclaw.active_run_registry import ActiveRunRegistry
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.skills_pool.center_content import (
    MountedCenterContentAdapter,
)


class _AsyncEventFakeClient:
    """Scripted-stream fake; behaves like `OpenClawGatewayClient.chat_stream`."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.connected = False

    async def chat_stream(self, **kwargs):  # noqa: ANN003
        for e in self._events:
            yield dict(e)


class _FakePool:
    def __init__(self, client: _AsyncEventFakeClient) -> None:
        self._client = client

    async def get(self, token: str | None = None) -> _AsyncEventFakeClient:
        return self._client


def _build_engine_with_stream(events: list[dict[str, Any]]) -> OpenClawEngine:
    registry = ActiveRunRegistry()
    client = _AsyncEventFakeClient(events)
    impl = OpenClawPluginImpl(
        center_content_adapter=MountedCenterContentAdapter(),
        pool=_FakePool(client),
        active_run_registry=registry,
    )
    engine = OpenClawEngine()
    # Swap in the test-friendly plugin impl (already wired with the registry)
    # so chat_stream + query surface live on the same registry instance.
    engine._port = impl
    return engine, registry


@pytest.fixture
def manager(monkeypatch):
    EngineManager.reset_instance()
    holder: dict[str, object] = {"engine": None}

    class _StubManager:
        engine = "openclaw"

        def active_engine_instance(self):
            return holder["engine"]

        async def status(self) -> dict:
            return {"engine": "openclaw", "active_connections": 0, "process": {}, "transition": None}

    stub = _StubManager()
    stub.engine = "openclaw"
    stub._holder = holder  # back-compat for direct attribute setting in tests
    monkeypatch.setattr(EngineManager, "get_instance", classmethod(lambda cls: stub))
    yield stub
    EngineManager.reset_instance()


@pytest.fixture
def app(manager):
    _app = FastAPI()
    _app.include_router(engine_router)
    return _app


class TestLifecycleActiveToClear:
    async def test_active_then_terminal_then_clear(self, manager, app):
        events = [
            {"state": "delta", "runId": "run-1"},
            {"state": "final", "runId": "run-1"},
        ]
        engine, registry = _build_engine_with_stream(events)
        manager._holder["engine"] = engine

        # Before the stream starts: clear.
        result = await engine.query_active_sessions()
        assert result["verdict"] == "clear"

        # Start a stream and stop mid-flight after the delta event.
        agen = engine._port.chat_stream(session_key="session:abc:user:1", message="m")
        await agen.__anext__()  # delta → registry now has 1 active run
        result = await engine.query_active_sessions()
        assert result["verdict"] == "active"
        assert result["active_session_count"] == 1

        # The HTTP route must agree with the engine.
        with TestClient(app) as client:
            resp = client.get("/api/engine/active-sessions")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["query_status"] == "ok"
            assert data["verdict"] == "active"

        # Drain the rest (final arrives) → registry releases the run.
        async for _ in agen:
            pass

        result = await engine.query_active_sessions()
        assert result["verdict"] == "clear"
        assert result["query_status"] == "ok"

    async def test_post_abort_yields_clear(self, manager):
        events = [
            {"state": "delta", "runId": "run-1"},
            {"state": "aborted", "runId": "run-1"},
        ]
        engine, registry = _build_engine_with_stream(events)
        manager._holder["engine"] = engine
        frames = [f async for f in engine._port.chat_stream(session_key="session:abc:user:1", message="m")]
        assert frames[-1].payload["state"] == "aborted"
        result = await engine.query_active_sessions()
        assert result["verdict"] == "clear"
        assert result["query_status"] == "ok"

    async def test_post_error_yields_clear(self, manager):
        events = [
            {"state": "delta", "runId": "run-1"},
            {"state": "error", "runId": "run-1"},
        ]
        engine, _ = _build_engine_with_stream(events)
        manager._holder["engine"] = engine
        frames = [f async for f in engine._port.chat_stream(session_key="session:abc:user:1", message="m")]
        assert frames[-1].payload["state"] == "error"
        result = await engine.query_active_sessions()
        assert result["verdict"] == "clear"


class TestMultiSessionIsolation:
    async def test_two_sessions_query_returns_both(self, manager):
        events_a = [{"state": "delta", "runId": "rA"}]
        events_b = [{"state": "delta", "runId": "rB"}]
        engine, _ = _build_engine_with_stream(events_a)
        manager._holder["engine"] = engine
        # second stream — same engine but a second chat_stream call against the
        # same plugin runtime would reuse the same fake; build a second impl
        # referencing the same engine registry via the engine's port. We model
        # the second conversation through manual registration of the registry
        # (since the engine shares one `_port`, the fake client streams one
        # scripted event sequence per call).
        second_impl = OpenClawPluginImpl(
            center_content_adapter=MountedCenterContentAdapter(),
            pool=_FakePool(_AsyncEventFakeClient(events_b)),
            active_run_registry=engine._port.active_run_registry,
        )
        a_gen = engine._port.chat_stream(session_key="session:sA:user:1", message="ma")
        b_gen = second_impl.chat_stream(session_key="session:sB:user:1", message="mb")
        await a_gen.__anext__()
        await b_gen.__anext__()
        result = await engine.query_active_sessions()
        assert result["verdict"] == "active"
        assert result["active_session_count"] == 2
        sessions = {e["session_id"]: e for e in result["sessions"]}
        assert set(sessions) == {"sA", "sB"}
        await a_gen.aclose()
        await b_gen.aclose()


class TestCompatRegression:
    def test_engine_status_route_unchanged(self, manager, app):
        with TestClient(app) as client:
            # The stub manager returns a stable /api/engine/status payload
            # (no ActiveSessionsResponseData leak into it).
            resp = client.get("/api/engine/status")
            assert resp.status_code == 200
            body = resp.json()
            assert "engine" in body
            assert body["engine"] == "openclaw"
            # No new keys were introduced into /status.
            assert set(body) <= {"engine", "active_connections", "process", "transition"}

    def test_active_sessions_route_added(self, manager, app):
        with TestClient(app) as client:
            resp = client.get("/api/engine/active-sessions")
            assert resp.status_code == 200
            assert resp.json()["data"]["engine"] == "openclaw"
