from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.api.transport.ws_server import EngineWebSocketServer
from engine.community.api.transport.openclaw_resume import OpenClawResumeRegistry
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import EventFrame, RequestFrame
from engine.community.manager import EngineManager
from engine.community.plugins.auth_gate.noop_impl import NoopAuthGateService


def _request(method: str, params: dict[str, object]) -> RequestFrame:
    return RequestFrame(id="req-1", method=method, params=params)


@pytest.mark.asyncio
async def test_replay_buffer_redacts_materialized_paths() -> None:
    server = EngineWebSocketServer()
    registry = OpenClawResumeRegistry()
    created = registry.create(session_key="session:one", run_id="run-1")
    assert created is not None
    run, _ = created
    private_path = "/private/workspace/session-files/story.txt"
    await server._emit_stream_event(
        SimpleNamespace(send_text=AsyncMock()),
        "agent",
        {"text": f"Reading {private_path}"},
        materialized_paths=(private_path,),
        resume_run=run,
    )
    assert private_path not in run._events[0]
    assert "Reading " in run._events[0]


@pytest.mark.asyncio
async def test_browser_disconnect_does_not_stop_resumable_openclaw_stream() -> None:
    EngineManager.reset_instance()
    manager = EngineManager("openclaw")
    started = asyncio.Event()
    continue_stream = asyncio.Event()
    seen_auth: list[AuthContext] = []

    async def stream(_request, *, auth: AuthContext):
        seen_auth.append(auth)
        yield EventFrame(
            event="agent",
            payload={"runId": "run-1", "state": "delta", "text": "first"},
        )
        started.set()
        await continue_stream.wait()
        yield EventFrame(
            event="chat",
            payload={"runId": "run-1", "state": "final", "text": "first second"},
        )

    engine = SimpleNamespace(
        chat=SimpleNamespace(stream=stream),
        on_connection_open=AsyncMock(),
        on_connection_close=AsyncMock(),
    )
    manager._active_engine = engine
    EngineManager._instance = manager
    server = EngineWebSocketServer()
    server._conn_auth["conn-old"] = AuthContext(token="pooled-token")
    server._subscribe_conn_to_session = AsyncMock(return_value=False)
    old_socket = SimpleNamespace(send_text=AsyncMock())
    params: dict[str, object] = {
        "sessionKey": "session:one",
        "message": "write a story",
        "idempotencyKey": "run-1",
        "resumeEnabled": True,
    }

    try:
        accepted = await server._handle_chat_send(
            old_socket,
            "conn-old",
            _request("chat.send", params),
            params,
            auth_gate_service=NoopAuthGateService(),
        )
        assert accepted.ok is True
        assert accepted.payload["accepted"] is True
        ticket = accepted.payload["resumeTicket"]
        await asyncio.wait_for(started.wait(), 2)
        await server._release_conn("conn-old")
        assert seen_auth == [AuthContext(token="pooled-token")]
        assert engine.on_connection_close.await_count == 1

        status = server._handle_chat_status(
            _request("chat.status", {}),
            {"sessionKey": "session:one", "resumeTicket": ticket},
        )
        assert status.payload["found"] is True
        assert status.payload["resumable"] is True
        denied = server._handle_chat_status(
            _request("chat.status", {}),
            {"sessionKey": "session:one", "resumeTicket": "bad-ticket"},
        )
        assert denied.payload == {"found": False, "reason": "NO_ACTIVE_RUN"}

        new_socket = SimpleNamespace(send_text=AsyncMock())
        restored = await server._handle_chat_resume(
            new_socket,
            "conn-new",
            _request("chat.resume", {}),
            {"sessionKey": "session:one", "resumeTicket": ticket},
        )
        assert restored.payload["resumed"] is True
        continue_stream.set()
        run = server._openclaw_resume.lookup(
            session_key="session:one", ticket=ticket
        )
        assert run is not None and run.task is not None
        await asyncio.wait_for(run.task, 2)
        await asyncio.sleep(0)

        frames = [
            json.loads(call.args[0])
            for call in new_socket.send_text.await_args_list
        ]
        assert [frame["payload"]["state"] for frame in frames] == ["delta", "final"]
        assert [frame["payload"]["resumeSeq"] for frame in frames] == [1, 2]
        assert engine.on_connection_open.await_count == 1
        assert engine.on_connection_close.await_count == 2
    finally:
        continue_stream.set()
        EngineManager.reset_instance()


@pytest.mark.asyncio
async def test_resumable_stream_failure_is_terminal_and_redacted() -> None:
    EngineManager.reset_instance()
    manager = EngineManager("openclaw")

    async def stream(_request, *, auth: AuthContext):
        raise RuntimeError("private upstream detail")
        yield  # pragma: no cover

    engine = SimpleNamespace(
        chat=SimpleNamespace(stream=stream),
        on_connection_open=AsyncMock(),
        on_connection_close=AsyncMock(),
    )
    manager._active_engine = engine
    EngineManager._instance = manager
    server = EngineWebSocketServer()
    server._conn_auth["conn-old"] = AuthContext(token="pooled-token")
    server._subscribe_conn_to_session = AsyncMock(return_value=False)
    socket = SimpleNamespace(send_text=AsyncMock())
    params: dict[str, object] = {
        "sessionKey": "session:one",
        "message": "write a story",
        "idempotencyKey": "run-1",
        "resumeEnabled": True,
    }

    try:
        accepted = await server._handle_chat_send(
            socket,
            "conn-old",
            _request("chat.send", params),
            params,
            auth_gate_service=NoopAuthGateService(),
        )
        ticket = accepted.payload["resumeTicket"]
        run = server._openclaw_resume.lookup(session_key="session:one", ticket=ticket)
        assert run is not None and run.task is not None
        await asyncio.wait_for(run.task, 2)

        assert run.state == "error"
        assert run.finished_at is not None
        assert len(run._events) == 1
        event = json.loads(run._events[0])
        assert event["payload"]["runId"] == "run-1"
        assert event["payload"]["errorMessage"] == "Chat stream failed."
        assert "private upstream detail" not in run._events[0]
        assert engine.on_connection_close.await_count == 1
    finally:
        EngineManager.reset_instance()
