"""Error-field additions must not change chat lifecycle or legacy messages."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.api.tests.transport.test_openclaw_chat_ack import (
    _fire_event, _start_resume, auth_gate_service, fake_engine, server, setup,
)
from engine.community.api.transport.chat_error_fields import complete_chat_error
from engine.community.kernel.frames import ErrorShape, ResponseFrame
from engine.community.manager import EngineManager
from engine.community.plugin_api.openclaw.chat import OpenClawChatAck


@pytest.mark.parametrize("code,expected", [
    (None, "UNAVAILABLE"), ("", "UNAVAILABLE"), ("  ", "UNAVAILABLE"),
    ("AGENT_TIMEOUT", "AGENT_TIMEOUT"), ("FUTURE_CODE", "FUTURE_CODE"),
    (-32001, "-32001"), (0, "0"),
])
def test_complete_error_preserves_message_extensions_and_input(code, expected):
    payload = {
        "state": "error", "runId": "r", "sessionKey": "s",
        "errorCode": code, "errorMessage": "[RESOURCE_NOT_FOUND] original message",
        "retryable": False, "details": {"source": "gateway"},
    }
    result = complete_chat_error(payload)
    assert result == {**payload, "errorCode": expected}
    assert payload["errorCode"] == code
    assert result is not payload


@pytest.mark.parametrize("message", [None, "", " ", 123])
def test_missing_error_message_uses_legacy_frontend_fallback(message):
    assert complete_chat_error({"errorMessage": message})["errorMessage"] == "Unknown error"


@pytest.mark.parametrize("engine", ["openclaw", "hermes", "claude_code", "aicoding", "deepseek_harness", "teclaw"])
@pytest.mark.parametrize("state", ["error", "delta", "final", "aborted"])
@pytest.mark.parametrize("resume", [False, True])
async def test_only_target_engine_error_payloads_change(server, fake_engine, engine, state, resume):
    EngineManager.get_instance()._engine = engine
    server._send_event = AsyncMock()
    run = SimpleNamespace(publish=AsyncMock()) if resume else None
    payload = {"state": state, "errorMessage": "original", "seq": 7, "ts": 10}
    await server._emit_stream_event(None, "chat", payload, resume_run=run)
    if resume:
        run.publish.assert_awaited_once()
        event, sent = run.publish.await_args.args
        server._send_event.assert_not_awaited()
    else:
        server._send_event.assert_awaited_once()
        _, event, sent = server._send_event.await_args.args
    assert event == "chat"
    expected = dict(payload)
    if engine in ("openclaw", "hermes") and state == "error":
        expected["errorCode"] = "UNAVAILABLE"
    assert sent == expected
    assert "errorCode" not in payload


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("code", [None, "AGENT_TIMEOUT", "FUTURE_CODE"])
async def test_upstream_error_is_one_terminal_with_original_identity_and_message(
    setup, server, auth_gate_service, code, resume,
):
    client, websocket, start = setup
    payload = {"runId": "actual", "sessionKey": "sk", "state": "error", "errorMessage": "original failure"}
    if code is not None:
        payload["errorCode"] = code

    async def send(*args, **kwargs):
        _fire_event(client, "chat", payload)
        return ResponseFrame.ok_response("upstream", {"runId": "actual", "status": "started"})

    client.send_request = send
    if resume:
        run = await _start_resume(server, websocket, auth_gate_service)
        await asyncio.wait_for(run.task, 1)
        assert run.state == "error"
        assert len(run._events) == 1
        assert json.loads(run._events[0])["payload"]["errorCode"] == (code or "UNAVAILABLE")
    else:
        await asyncio.wait_for(asyncio.gather(*await start()), 1)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.await_args_list]
    assert len(frames) == 2
    assert frames[0]["ok"] is True
    assert frames[1]["payload"].items() >= {
        **payload, "errorCode": code or "UNAVAILABLE",
    }.items()
    assert frames[1]["event"] == "chat"
    assert not any(client._event_listeners.values())


@pytest.mark.parametrize("error", [None, ErrorShape("", ""), ErrorShape("FUTURE", "original", details={"a": 1}, retryable=False)])
@pytest.mark.parametrize("resume", [False, True])
async def test_negative_ack_completed_without_extra_event(setup, server, auth_gate_service, error, resume):
    client, websocket, start = setup
    client.send_request = AsyncMock(return_value=ResponseFrame(id="upstream", ok=False, error=error))
    if resume:
        run = await _start_resume(server, websocket, auth_gate_service)
        await asyncio.wait_for(run.task, 1)
        assert not run._events
    else:
        await asyncio.wait_for(asyncio.gather(*await start()), 1)
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.await_args.args[0])
    assert frame["ok"] is False
    assert frame["error"]["code"] == (error.code if error and error.code else "UNKNOWN")
    assert frame["error"]["message"] == (error.message if error and error.message else "Unknown error")
    if error and error.code:
        assert frame["error"] == error.to_dict()
    assert not any(client._event_listeners.values())


def test_ack_normalization_does_not_mutate_upstream_error():
    error = ErrorShape(-32001, "unchanged", retry_after_ms=15)
    frame = OpenClawChatAck(False, {}, error).response("req")
    assert frame.error.code == "-32001"
    assert frame.error.retry_after_ms == 15
    assert error.code == -32001


@pytest.mark.parametrize("resume", [False, True])
async def test_startup_exception_keeps_existing_ack_code_and_sanitization(setup, server, auth_gate_service, resume):
    client, websocket, start = setup
    client.send_request = AsyncMock(side_effect=ConnectionError("private detail"))
    if resume:
        run = await _start_resume(server, websocket, auth_gate_service)
        await asyncio.wait_for(run.task, 1)
        assert not run._events
    else:
        await asyncio.wait_for(asyncio.gather(*await start()), 1)
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.await_args.args[0])
    assert frame["error"] == {
        "code": "UNAVAILABLE",
        "message": "Chat stream failed." if resume else "private detail",
    }


@pytest.mark.parametrize("resume", [False, True])
async def test_exception_after_successful_ack_is_one_error_event(
    setup, server, auth_gate_service, fake_engine, resume,
):
    _, websocket, start = setup

    async def stream(request, auth=None, *, on_ack=None):
        await on_ack(OpenClawChatAck(True, {"runId": "actual", "status": "started"}))
        raise RuntimeError("original detail")
        yield  # pragma: no cover - async-generator shape

    fake_engine.chat = SimpleNamespace(stream=stream)
    if resume:
        run = await _start_resume(server, websocket, auth_gate_service)
        await asyncio.wait_for(run.task, 1)
        assert len(run._events) == 1
        assert run.state == "error"
    else:
        await asyncio.wait_for(asyncio.gather(*await start()), 1)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.await_args_list]
    assert len(frames) == 2
    assert frames[0]["type"] == "res" and frames[0]["ok"] is True
    assert frames[1]["type"] == "event"
    assert frames[1]["payload"]["state"] == "error"
    assert frames[1]["payload"]["errorCode"] == "UNAVAILABLE"
    assert frames[1]["payload"]["errorMessage"] == (
        "Chat stream failed." if resume else "original detail"
    )
