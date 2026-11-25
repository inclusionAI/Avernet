"""A2A activity must be visible through the transport-neutral HTTP observer."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

import engine.community.api.session.runtime_status as status_api
import engine.community.api.transport.a2a.engine_bridge as bridge
import engine.community.api.transport.a2a.router as a2a_router
from engine.community.api.transport.a2a.kernel import A2aKernel
from engine.community.api.transport.ws_server import EngineWebSocketServer
from engine.community.kernel.frames import ResponseFrame


@pytest.fixture
async def observer(monkeypatch):
    kernel = A2aKernel()
    server = EngineWebSocketServer()
    relay = SimpleNamespace(forward_request=AsyncMock(
        return_value=ResponseFrame.ok_response("access", {})))
    monkeypatch.setattr(a2a_router, "_kernel", kernel)
    monkeypatch.setattr(status_api, "get_server", lambda: server)
    monkeypatch.setattr(status_api.EngineManager, "get_instance", lambda: SimpleNamespace(
        engine="openclaw", relay=relay))
    app = FastAPI()
    app.include_router(status_api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://engine") as client:
        async def query(session="s"):
            response = await client.get("/api/sessions/runtime/status",
                                        params={"session_id": session})
            assert response.status_code == 200
            return response.json()["data"]
        yield kernel, server, query


@pytest.mark.asyncio
async def test_a2a_registration_visible_and_queries_are_read_only(observer):
    kernel, _, query = observer
    rid, _ = await kernel.open_run("s", "key", "first")
    before = await query()
    assert before == {
        "session_id": "s", "found": True, "run_id": rid, "state": "starting",
        "started_at": kernel.runs[rid].started_at_ms, "last_event_seq": 1,
        "reason": None,
    }
    assert await query() == before
    assert not kernel.runs[rid].subscribers
    assert (await query("other"))["reason"] == "NO_ACTIVE_RUN"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["tool", "assistant", "lifecycle"])
async def test_real_a2a_bridge_events_advance_http_sequence(observer, monkeypatch, stream):
    kernel, _, query = observer
    rid, _ = await kernel.open_run("s", "key", "first")
    observed = []

    async def frames():
        observed.append(await query())
        for phase in ("start", "update", "result"):
            yield SimpleNamespace(event="agent", payload={
                "runId": "native-run", "stream": stream,
                "data": {"phase": phase, "toolCallId": "call-1"}})
            observed.append(await query())
        yield SimpleNamespace(event="chat", payload={
            "state": "final", "message": {"content": [{"type": "text", "text": "done"}]}})

    chat = SimpleNamespace(stream=AsyncMock(side_effect=lambda *a, **kw: frames()))
    monkeypatch.setattr(bridge, "_chat_plugin", lambda: chat)
    await bridge.execute_and_stream(kernel, rid, "s", "question")
    assert [item["last_event_seq"] for item in observed] == [2, 3, 4, 5]
    assert all(item["state"] == "running" for item in observed)
    assert all(item["run_id"] == rid for item in observed)
    assert len({item["started_at"] for item in observed}) == 1
    assert (await query())["state"] == "final"
    chat.stream.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [
    ("TASK_STATE_WORKING", "running"),
    ("TASK_STATE_INPUT_REQUIRED", "running"),
    ("TASK_STATE_AUTH_REQUIRED", "running"),
    ("TASK_STATE_COMPLETED", "final"),
    ("TASK_STATE_FAILED", "error"),
    ("TASK_STATE_REJECTED", "error"),
    ("TASK_STATE_CANCELED", "aborted"),
])
async def test_a2a_state_mapping(observer, state, expected):
    kernel, _, query = observer
    rid, _ = await kernel.open_run("s", "key", "first")
    if expected in {"final", "error", "aborted"}:
        await kernel.complete_run(rid, state)
    else:
        await kernel.publish_status(rid, state)
    assert (await query())["state"] == expected


@pytest.mark.asyncio
async def test_a2a_expired_terminal_and_cache_limit(observer):
    kernel, _, query = observer
    rid, _ = await kernel.open_run("s", "key", "first")
    run = kernel.runs[rid]
    run.cache_limit = True
    run.events.clear()
    before = (await query())["last_event_seq"]
    await kernel.publish_agent_progress(rid, {"stream": "tool"})
    assert (await query())["last_event_seq"] == before + 1
    await kernel.complete_run(rid)
    assert (await query())["state"] == "final"
    run.replay_expires_at = time.monotonic() - 1
    assert (await query())["reason"] == "NO_ACTIVE_RUN"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["two_a2a", "both", "ws_ambiguous"])
async def test_multiple_active_runs_are_ambiguous(observer, case):
    kernel, server, query = observer
    await kernel.open_run("s", "key", "first")
    if case == "two_a2a":
        await kernel.open_run("s", "other", "first")
    else:
        server._openclaw_resume.create(session_key="s", run_id="ws")
        if case == "ws_ambiguous":
            server._openclaw_resume.create(session_key="s", run_id="ws2")
    assert await query() == {"session_id": "s", "found": False, "reason": "AMBIGUOUS_RUN"}


@pytest.mark.asyncio
@pytest.mark.parametrize("active_transport", ["a2a", "ws"])
async def test_active_record_wins_over_other_transport_terminal(observer, active_transport):
    kernel, server, query = observer
    rid, _ = await kernel.open_run("s", "key", "first")
    run, _ = server._openclaw_resume.create(session_key="s", run_id="ws")
    if active_transport == "a2a":
        await run.publish("chat", {"state": "final"})
        await run.finish()
        expected = rid
    else:
        await kernel.complete_run(rid)
        expected = "ws"
    assert (await query())["run_id"] == expected


@pytest.mark.asyncio
async def test_latest_a2a_terminal_and_new_active_run(observer):
    kernel, _, query = observer
    old, _ = await kernel.open_run("s", "old", "first")
    await kernel.complete_run(old)
    new, _ = await kernel.open_run("s", "new", "first")
    assert (await query())["run_id"] == new
    await kernel.complete_run(new)
    assert (await query())["run_id"] == new


@pytest.mark.asyncio
async def test_denied_access_never_reads_a2a_registry(observer, monkeypatch):
    _, _, _ = observer
    def fail_if_read(*args):
        pytest.fail("unauthorized request read A2A state")
    monkeypatch.setattr(a2a_router, "get_session_runtime_status", fail_if_read)
    manager = status_api.EngineManager.get_instance()
    from engine.community.kernel.frames import ErrorShape
    manager.relay.forward_request.return_value = ResponseFrame.err_response(
        "access", ErrorShape("FORBIDDEN", "private"))
    app = FastAPI()
    app.include_router(status_api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://engine") as client:
        response = await client.get("/api/sessions/runtime/status", params={"session_id": "s"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rpc_send_bridge_tool_activity_and_http_status(observer, monkeypatch):
    """Exercise real RPC registration and dispatch, not a seeded kernel run."""
    from fastapi_injector import attach_injector
    from injector import Injector
    from engine.community.plugin_api.auth_gate.protocol import AuthGateService

    kernel, server, query = observer
    started = asyncio.Event()
    proceed = asyncio.Event()
    finished = asyncio.Event()

    async def frames():
        try:
            yield SimpleNamespace(event="agent", payload={
                "stream": "tool", "data": {"phase": "start", "name": "exec"}})
            started.set()
            await proceed.wait()
            yield SimpleNamespace(event="agent", payload={
                "stream": "tool", "data": {"phase": "result", "name": "exec"}})
            yield SimpleNamespace(event="chat", payload={"state": "final"})
        finally:
            finished.set()

    chat = SimpleNamespace(stream=AsyncMock(side_effect=lambda *a, **kw: frames()))
    monkeypatch.setattr(bridge, "_chat_plugin", lambda: chat)
    app = FastAPI()
    gate = SimpleNamespace(get_switch=AsyncMock(return_value=False))
    injector = Injector()
    injector.binder.bind(AuthGateService, to=gate)
    attach_injector(app, injector)
    app.include_router(a2a_router.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://engine") as client:
        try:
            response = await client.post("/rpc", json={
                "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
                "params": {"message": {"messageId": "rpc-key", "contextId": "s",
                                       "role": "ROLE_USER", "parts": [{"text": "question"}]}}})
            rid = response.json()["result"]["task"]["id"]
            await asyncio.wait_for(started.wait(), 2)
            active = await query()
            assert active["found"] is True
            assert active["run_id"] == rid
            assert active["state"] == "running"
            assert active["last_event_seq"] == 3
            assert server.get_session_runtime_status("s")["found"] is False
            proceed.set()
            await asyncio.wait_for(finished.wait(), 2)
            # Join the router-owned dispatch tasks before checking completion.
            await asyncio.gather(*list(a2a_router._BRIDGE_TASKS))
            terminal = await query()
            assert terminal["state"] == "final"
            assert terminal["last_event_seq"] > active["last_event_seq"]
            assert terminal["started_at"] == active["started_at"]
            chat.stream.assert_awaited_once()
        finally:
            proceed.set()
            await asyncio.gather(*list(a2a_router._BRIDGE_TASKS))


@pytest.mark.asyncio
async def test_both_transports_terminal_choose_latest_started_record(observer):
    kernel, server, query = observer
    rid, _ = await kernel.open_run("s", "key", "first")
    await kernel.complete_run(rid)
    run, _ = server._openclaw_resume.create(session_key="s", run_id="ws")
    run.started_at_ms = kernel.runs[rid].started_at_ms + 1
    await run.publish("chat", {"state": "final"})
    await run.finish()
    assert (await query())["run_id"] == "ws"
    kernel.runs[rid].started_at_ms = run.started_at_ms + 1
    assert (await query())["run_id"] == rid
