"""Exercise ACK ordering through WS -> adapter -> port -> gateway client."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

from engine.community.api.tests.transport.test_ws_server import (
    _req, auth_gate_service, fake_engine, server,
)
from engine.community.core.adapters.openclaw.chat import OpenClawChatAdapter
from engine.community.kernel.frames import ErrorShape, EventFrame, ResponseFrame
from engine.community.local.openclaw.plugin_impl import LocalOpenClawPluginImpl
from engine.community.openclaw.client.tests.test_gateway_client import _fire_event, _make_client
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


@pytest.fixture
def setup(server, fake_engine, auth_gate_service, monkeypatch):
    monkeypatch.setattr(
        "engine.community.core.adapters.openclaw.chat.IntentEvalObserver", MagicMock()
    )
    client = _make_client()
    port = OpenClawPluginImpl(pool=SimpleNamespace(get=AsyncMock(return_value=client)))
    fake_engine.chat = OpenClawChatAdapter(port)
    server._subscribe_conn_to_session = AsyncMock(return_value=True)
    auth_gate_service.enabled = False
    websocket = SimpleNamespace(send_text=AsyncMock())

    async def start():
        params = {"sessionKey": "sk", "message": "hello", "idempotencyKey": "new"}
        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )
        assert response is None
        return list(server._ack_chat_tasks["conn-1"])

    return client, websocket, start


@pytest.mark.parametrize("status,cached", [("started", False), ("in_flight", True), ("future", None)])
async def test_actual_ack_precedes_early_final(setup, status, cached):
    client, websocket, start = setup
    async def send(*args, **kwargs):
        _fire_event(client, "chat", {
            "runId": "actual", "sessionKey": "sk", "state": "final",
            "message": {"content": [{"type": "text", "text": "answer"}]},
        })
        return ResponseFrame.ok_response("upstream-id", {"runId": "actual", "status": status})
    client.send_request = send
    await asyncio.wait_for(asyncio.gather(*await start()), 1)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.call_args_list]
    assert len(frames) == 2
    assert frames[0]["id"] == "r-1"
    assert frames[0]["ok"] is True
    assert frames[0]["payload"] == {
        "runId": "actual", "status": status, "accepted": True, "cached": cached,
    }
    assert frames[1]["payload"]["state"] == "final"
    assert frames[1]["payload"]["runId"] == "actual"
    assert not any(client._event_listeners.values())


@pytest.mark.parametrize("ok,status,cached", [(True, "ok", True), (False, "error", None)])
async def test_terminal_ack_preserves_failure_without_fabricating_final(setup, ok, status, cached):
    client, websocket, start = setup
    client.send_request = AsyncMock(return_value=ResponseFrame(
        id="upstream", ok=ok, payload={"runId": "old", "status": status},
        error=None if ok else ErrorShape("DENIED", "original failure", retryable=False),
    ))
    await asyncio.wait_for(asyncio.gather(*await start()), 1)
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.call_args.args[0])
    assert frame["id"] == "r-1"
    assert frame["ok"] is ok
    assert frame["payload"]["accepted"] is ok
    assert frame["payload"]["cached"] is cached
    if not ok:
        assert frame["error"]["code"] == "DENIED"
        assert frame["error"]["message"] == "original failure"
    assert not any(client._event_listeners.values())


async def test_startup_failure_is_failed_ack(setup):
    client, websocket, start = setup
    client.send_request = AsyncMock(side_effect=TimeoutError("upstream timeout"))
    await asyncio.gather(*await start())
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.call_args.args[0])
    assert frame["id"] == "r-1"
    assert frame["ok"] is False
    assert frame["payload"] == {"accepted": False, "cached": None}
    assert not any(client._event_listeners.values())


@pytest.mark.parametrize("after_ack", [False, True])
async def test_disconnect_cancels_pending_stream_and_releases_listeners(setup, server, after_ack):
    client, websocket, start = setup
    ready = asyncio.Event()
    async def send(*args, **kwargs):
        if after_ack:
            return ResponseFrame.ok_response("upstream", {"runId": "actual", "status": "started"})
        ready.set()
        await asyncio.Event().wait()
    client.send_request = send
    async def write(text):
        ready.set()
    websocket.send_text.side_effect = write
    tasks = await start()
    await asyncio.wait_for(ready.wait(), 1)
    await server._release_conn("conn-1")
    assert all(task.done() for task in tasks)
    assert "conn-1" not in server._ack_chat_tasks
    assert not any(client._event_listeners.values())
    assert websocket.send_text.await_count == int(after_ack)


async def test_failed_ack_write_does_not_retry_ack_or_leak_listeners(setup):
    client, websocket, start = setup
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response(
        "upstream", {"runId": "actual", "status": "started"},
    ))
    websocket.send_text.side_effect = ConnectionError("closed")
    await asyncio.wait_for(asyncio.gather(*await start()), 1)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.call_args_list]
    assert len([frame for frame in frames if frame["type"] == "res"]) == 1
    assert not any(client._event_listeners.values())


async def test_local_openclaw_implements_ack_contract(setup, fake_engine):
    _, websocket, start = setup
    fake_engine.chat = OpenClawChatAdapter(LocalOpenClawPluginImpl())
    await asyncio.wait_for(asyncio.gather(*await start()), 1)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.call_args_list]
    assert frames[0]["payload"]["cached"] is False
    assert frames[0]["payload"]["status"] == "started"
    assert frames[-1]["payload"]["content"] == "local noop"


@pytest.mark.parametrize("raises,write_fails", [(False, False), (True, False), (True, True)])
async def test_missing_ack_fallback(server, raises, write_fails):
    server._stream_chat_events = AsyncMock(side_effect=RuntimeError("startup") if raises else None)
    websocket = SimpleNamespace(send_text=AsyncMock(
        side_effect=ConnectionError("closed") if write_fails else None,
    ))
    await server._run_ack_chat(websocket, "conn-1", "request", "sk", "hello")
    websocket.send_text.assert_awaited_once()
    frame = json.loads(websocket.send_text.call_args.args[0])
    assert frame["id"] == "request"
    assert frame["ok"] is False
    assert frame["payload"]["accepted"] is False


async def test_message_loop_accepts_deferred_ack_and_keeps_normal_responses(server, auth_gate_service):
    websocket = SimpleNamespace(
        send_text=AsyncMock(),
        receive_text=AsyncMock(side_effect=[
            _req("chat.send", {}).to_json(),
            _req("chat.abort", {}, id="abort").to_json(),
            WebSocketDisconnect(),
        ]),
    )
    server._tick_loop = AsyncMock()
    server._handle_request = AsyncMock(side_effect=[
        (None, []),
        (ResponseFrame.ok_response("abort", {}), [("chat", {"state": "aborted"})]),
    ])
    with pytest.raises(WebSocketDisconnect):
        await server._message_loop(websocket, "conn-1", auth_gate_service=auth_gate_service)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.call_args_list]
    assert [frame["type"] for frame in frames] == ["res", "event"]
    assert frames[0]["id"] == "abort"


@pytest.mark.parametrize("live", [True, False])
async def test_inject_delivery_does_not_stop_foreground_stream(server, live):
    websocket = SimpleNamespace(send_text=AsyncMock())
    server._connection_has_live_inject_listener = MagicMock(return_value=live)
    async def frames():
        yield EventFrame(event="chat", payload={"runId": "inject-a", "state": "final"})
        yield EventFrame(event="chat", payload={"runId": "r", "state": "final"})
    error = AsyncMock()
    await server._relay_chat_stream(websocket, "conn-1", "sk", frames(), (), True, lambda: True, error)
    assert websocket.send_text.await_count == (1 if live else 2)
    assert json.loads(websocket.send_text.call_args.args[0])["payload"]["runId"] == "r"
    error.assert_not_awaited()
