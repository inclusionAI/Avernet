import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.api.transport.openclaw_resume import OpenClawResumeRegistry
from engine.community.api.transport.openclaw_session_resume import resume_session
from engine.community.api.transport.ws_server import EngineWebSocketServer
from engine.community.kernel.frames import ErrorShape, RequestFrame, ResponseFrame
from engine.community.manager import EngineManager
from engine.community.plugins.auth_gate.noop_impl import NoopAuthGateService


def request(**params):
    return RequestFrame(id="resume-1", method="chat.resume", params=params)


@pytest.mark.asyncio
async def test_session_replay_then_live_and_completed_replay():
    registry = OpenClawResumeRegistry()
    run, ticket = registry.create(session_key="session:one", run_id="run-1")
    await run.publish("agent", {"runId": "run-1", "text": "hello"})
    read = AsyncMock(return_value=ResponseFrame.ok_response("resume-1", {}))
    socket = SimpleNamespace(send_text=AsyncMock())
    result = await resume_session(registry, socket, "c1", request(sessionKey="session:one"), read)
    assert result.payload["resumed"] is True
    assert result.payload["replayCount"] == 1
    assert result.payload["startedAt"] == run.started_at_ms
    assert read.await_args.args[0].params == {"sessionKey": "session:one", "limit": 1}
    await run.publish("chat", {"runId": "run-1", "state": "final"})
    await run.finish()
    await asyncio.sleep(0)
    assert [json.loads(c.args[0])["payload"]["resumeSeq"] for c in socket.send_text.await_args_list] == [1, 2]
    assert registry.lookup(session_key="session:one", ticket=ticket) is run
    completed = await resume_session(registry, socket, "c2", request(sessionKey="session:one"), read)
    assert completed.payload["state"] == "final"
    assert completed.payload["startedAt"] == result.payload["startedAt"]
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "  ", 123])
async def test_invalid_session_does_not_probe(key):
    read = AsyncMock()
    result = await resume_session(OpenClawResumeRegistry(), None, "c", request(sessionKey=key), read)
    assert not result.ok
    read.assert_not_called()


@pytest.mark.asyncio
async def test_read_denied_never_attaches_or_exposes_cache():
    registry = OpenClawResumeRegistry()
    run, _ = registry.create(session_key="sk", run_id="r")
    denial = ResponseFrame.err_response("resume-1", ErrorShape("FORBIDDEN", "Denied"))
    result = await resume_session(registry, None, "c", request(sessionKey="sk"), AsyncMock(return_value=denial))
    assert result is denial
    assert not run._subscribers


@pytest.mark.asyncio
async def test_missing_ambiguous_and_cache_limit():
    registry = OpenClawResumeRegistry()
    read = AsyncMock(return_value=ResponseFrame.ok_response("resume-1", {}))
    async def resume():
        return await resume_session(registry, None, "c", request(sessionKey="sk"), read)
    assert (await resume()).payload["reason"] == "NO_ACTIVE_RUN"
    first, _ = registry.create(session_key="sk", run_id="r1")
    second, _ = registry.create(session_key="sk", run_id="r2")
    assert (await resume()).error.code == "AMBIGUOUS_RUN"
    await first.finish()
    assert registry.lookup_session("sk") is second
    second.reason = "CACHE_LIMIT"
    assert (await resume()).payload["reason"] == "CACHE_LIMIT"
    await second.finish()
    assert registry.lookup_session("sk") is second
    second.finished_at -= 181
    assert registry.lookup_session("sk") is first


@pytest.mark.asyncio
@pytest.mark.parametrize("ticket", ["wrong", "", None])
async def test_supplied_invalid_ticket_never_falls_back(ticket):
    server = EngineWebSocketServer()
    server._openclaw_resume.create(session_key="sk", run_id="r")
    server._forward_request = AsyncMock()
    req = request(sessionKey="sk", resumeTicket=ticket)
    result = await server._handle_chat_resume(None, "c", req, req.params)
    assert result.payload == {"resumed": False, "reason": "NO_ACTIVE_RUN"}
    server._forward_request.assert_not_called()


@pytest.mark.asyncio
async def test_no_ticket_uses_connection_history_path():
    server = EngineWebSocketServer()
    server._forward_request = AsyncMock(return_value=(ResponseFrame.ok_response("resume-1", {}), []))
    req = request(sessionKey="sk")
    result = await server._handle_chat_resume(None, "caller", req, req.params)
    assert result.payload["resumed"] is False
    assert server._forward_request.await_args.args[0] == "caller"
    assert server._forward_request.await_args.args[1].method == "chat.history"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["hermes", "claude_code", "deepseek_harness", "aicoding"])
async def test_other_engines_keep_relay_dispatch(engine):
    EngineManager.reset_instance()
    manager = EngineManager(engine)
    EngineManager._instance = manager
    server = EngineWebSocketServer()
    reply = ResponseFrame.ok_response("resume-1", {"native": True})
    server._forward_request = AsyncMock(return_value=(reply, []))
    server._handle_chat_resume = AsyncMock()
    try:
        result, _ = await server._handle_request(
            None, "c", request(sessionKey="sk"), auth_gate_service=NoopAuthGateService()
        )
        assert result is reply
        server._handle_chat_resume.assert_not_called()
    finally:
        EngineManager.reset_instance()
