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
from engine.community.manager import EngineManager
from engine.community.local.openclaw.plugin_impl import LocalOpenClawPluginImpl
from engine.community.openclaw.client.tests.test_gateway_client import _fire_event, _make_client
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


@pytest.fixture
def setup(server, fake_engine, auth_gate_service, monkeypatch):
    monkeypatch.setattr(
        "engine.community.core.adapters.openclaw.chat.IntentEvalObserver", MagicMock()
    )
    client = _make_client()
    port = OpenClawPluginImpl(
        pool=SimpleNamespace(get=AsyncMock(return_value=client)),
        center_content_adapter=MagicMock(),
    )
    fake_engine.chat = OpenClawChatAdapter(port)
    EngineManager.get_instance()._engine = "openclaw"
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
async def test_only_successful_cached_ack_emits_notice(setup, ok, status, cached):
    client, websocket, start = setup
    client.send_request = AsyncMock(return_value=ResponseFrame(
        id="upstream", ok=ok, payload={"runId": "old", "status": status},
        error=None if ok else ErrorShape("DENIED", "original failure", retryable=False),
    ))
    await asyncio.wait_for(asyncio.gather(*await start()), 1)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.call_args_list]
    frame = frames[0]
    assert frame["id"] == "r-1"
    assert frame["ok"] is ok
    assert frame["payload"]["accepted"] is ok
    assert frame["payload"]["cached"] is cached
    if not ok:
        assert frame["error"]["code"] == "DENIED"
        assert frame["error"]["message"] == "original failure"
        assert len(frames) == 1
    else:
        assert len(frames) == 3
        assert [f["payload"]["state"] for f in frames[1:]] == ["delta", "final"]
        assert all(f["event"] == "chat" for f in frames[1:])
        assert frames[1]["payload"]["message"] == frames[2]["payload"]["message"]
        assert frames[1]["payload"]["deltaText"] == frames[1]["payload"]["message"]["content"][0]["text"]
        assert "deltaText" not in frames[2]["payload"]
        for event in frames[1:]:
            payload = event["payload"]
            assert payload["runId"] == "old"
            assert payload["sessionKey"] == "sk"
            assert payload["cached"] is True
            assert payload["adapterNotice"] == "cached_request"
            assert payload["message"]["role"] == "assistant"
            assert payload["message"]["content"] == [{
                "type": "text",
                "text": "系统提示：本次请求命中了已有请求记录，未启动新的执行。"
                        "本次未返回回答正文，请查看原会话。此提示不代表原任务已完成。",
            }]
    client.send_request.assert_awaited_once()
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


async def _start_resume(server, websocket, auth_gate_service):
    params = {
        "sessionKey": "sk", "message": "hello", "idempotencyKey": "new",
        "resumeEnabled": True,
    }
    response = await server._handle_chat_send(
        websocket, "conn-1", _req("chat.send", params), params,
        auth_gate_service=auth_gate_service,
    )
    assert response is None
    return next(iter(server._openclaw_resume._runs.values()))


@pytest.mark.parametrize("ok,status", [(True, "ok"), (False, "error")])
async def test_resumable_terminal_ack_only_records_cached_notice(
    setup, server, auth_gate_service, fake_engine, ok, status,
):
    client, websocket, _ = setup
    client.send_request = AsyncMock(return_value=ResponseFrame(
        id="upstream", ok=ok, payload={"runId": "old", "status": status},
        error=None if ok else ErrorShape("DENIED", "denied"),
    ))
    run = await _start_resume(server, websocket, auth_gate_service)
    await asyncio.wait_for(run.task, 1)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.await_args_list]
    assert len(frames) == (3 if ok else 1)
    assert frames[0]["type"] == "res"
    assert frames[0]["ok"] is ok
    assert frames[0]["payload"]["status"] == status
    assert frames[0]["payload"]["runId"] == "old"
    ticket = frames[0]["payload"]["resumeTicket"]
    assert server._openclaw_resume.lookup(session_key="sk", ticket=ticket) is run
    assert run.state == ("final" if ok else "error")
    assert run.finished_at is not None
    assert len(run._events) == (2 if ok else 0)
    assert [json.loads(event)["payload"]["state"] for event in run._events] == (
        ["delta", "final"] if ok else []
    )
    assert not any(client._event_listeners.values())
    fake_engine.on_connection_close.assert_awaited_once()


@pytest.mark.parametrize("ack_write_fails", [False, True])
async def test_reused_run_survives_resume_disconnect_with_actual_identity(
    setup, server, auth_gate_service, fake_engine, ack_write_fails,
):
    client, websocket, _ = setup
    received_ack = asyncio.Event()

    async def write(_text):
        received_ack.set()
        if ack_write_fails:
            raise ConnectionError("closed")

    websocket.send_text.side_effect = write
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response(
        "upstream", {"runId": "old", "status": "in_flight"},
    ))
    run = await _start_resume(server, websocket, auth_gate_service)
    await asyncio.wait_for(received_ack.wait(), 1)
    ack = json.loads(websocket.send_text.await_args_list[0].args[0])
    assert ack["payload"]["cached"] is True
    assert ack["payload"]["runId"] == run.run_id == "old"
    await server._release_conn("conn-1")
    assert not run.task.done()
    _fire_event(client, "chat", {
        "runId": "old", "sessionKey": "sk", "state": "final", "text": "answer",
    })
    await asyncio.wait_for(run.task, 1)
    assert run.state == "final"
    new_socket = SimpleNamespace(send_text=AsyncMock())
    result = await server._handle_chat_resume(
        new_socket, "conn-2", _req("chat.resume", {}),
        {"sessionKey": "sk", "resumeTicket": ack["payload"]["resumeTicket"]},
    )
    assert result.payload["runId"] == "old"
    await asyncio.sleep(0)
    frame = json.loads(new_socket.send_text.await_args_list[0].args[0])
    assert frame["payload"]["state"] == "final"
    assert frame["payload"]["resumeSeq"] == 1
    assert not any(client._event_listeners.values())
    assert fake_engine.on_connection_close.await_count == 2


async def test_resumable_startup_failure_is_only_failed_ack(setup, server, auth_gate_service):
    client, websocket, _ = setup
    client.send_request = AsyncMock(side_effect=TimeoutError("private upstream detail"))
    run = await _start_resume(server, websocket, auth_gate_service)
    await asyncio.wait_for(run.task, 1)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.await_args_list]
    assert len(frames) == 1
    assert frames[0]["ok"] is False
    assert frames[0]["error"]["message"] == "Chat stream failed."
    assert run.state == "error"
    assert run.finished_at is not None
    assert not run._events


async def test_cached_notice_is_replayed_after_initial_socket_is_lost(
    setup, server, auth_gate_service,
):
    client, websocket, _ = setup
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response(
        "upstream", {"runId": "old", "status": "ok"},
    ))
    websocket.send_text.side_effect = ConnectionError("disconnected")
    run = await _start_resume(server, websocket, auth_gate_service)
    await asyncio.wait_for(run.task, 1)
    ack = json.loads(websocket.send_text.await_args_list[0].args[0])
    await server._release_conn("conn-1")
    new_socket = SimpleNamespace(send_text=AsyncMock())
    result = await server._handle_chat_resume(
        new_socket, "conn-2", _req("chat.resume", {}),
        {"sessionKey": "sk", "resumeTicket": ack["payload"]["resumeTicket"]},
    )
    assert result.payload["state"] == "final"
    await asyncio.sleep(0)
    events = [json.loads(call.args[0]) for call in new_socket.send_text.await_args_list]
    assert [event["payload"]["state"] for event in events] == ["delta", "final"]
    assert [event["payload"]["resumeSeq"] for event in events] == [1, 2]
    assert all(event["payload"]["adapterNotice"] == "cached_request" for event in events)
    client.send_request.assert_awaited_once()
    assert not any(client._event_listeners.values())


async def test_fresh_socket_reattaches_in_flight_run_without_early_final(setup, server):
    client, old_socket, start = setup
    acknowledged = asyncio.Event()

    async def write(_text):
        acknowledged.set()

    old_socket.send_text.side_effect = write
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response(
        "upstream", {"runId": "old", "status": "in_flight"},
    ))
    old_tasks = await start()
    await asyncio.wait_for(acknowledged.wait(), 1)
    await server._release_conn("conn-1")
    assert all(task.done() for task in old_tasks)

    acknowledged.clear()
    new_socket = SimpleNamespace(send_text=AsyncMock(side_effect=write))
    task = asyncio.create_task(server._run_ack_chat(
        new_socket, "conn-2", "new-request", "sk", "hello", None, "new-key",
    ))
    await asyncio.wait_for(acknowledged.wait(), 1)
    assert not task.done()
    assert new_socket.send_text.await_count == 1
    _fire_event(client, "chat", {
        "runId": "unrelated", "sessionKey": "sk", "state": "final", "text": "wrong",
    })
    for state in ("delta", "final"):
        _fire_event(client, "chat", {
            "runId": "old", "sessionKey": "sk", "state": state,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
        })
    await asyncio.wait_for(task, 1)
    frames = [json.loads(call.args[0]) for call in new_socket.send_text.await_args_list]
    assert frames[0]["id"] == "new-request"
    assert frames[0]["payload"]["runId"] == "old"
    assert [event["payload"]["state"] for event in frames[1:]] == ["delta", "final"]
    assert all(event["payload"]["runId"] == "old" for event in frames[1:])
    assert all("adapterNotice" not in event["payload"] for event in frames[1:])
    assert not any(client._event_listeners.values())


async def test_resumable_missing_ack_finishes_record(setup, server, auth_gate_service, fake_engine):
    _, websocket, _ = setup
    server._stream_chat_events = AsyncMock()
    run = await _start_resume(server, websocket, auth_gate_service)
    await asyncio.wait_for(run.task, 1)
    frame = json.loads(websocket.send_text.await_args.args[0])
    assert frame["ok"] is False
    assert frame["payload"]["accepted"] is False
    assert run.state == "error"
    assert run.finished_at is not None
    fake_engine.on_connection_close.assert_awaited_once()


@pytest.mark.parametrize("after_ack", [False, True])
async def test_cancelled_resume_keeps_ack_order_and_releases_upstream(
    setup, server, auth_gate_service, fake_engine, after_ack,
):
    client, websocket, _ = setup
    ready = asyncio.Event()

    async def send(*args, **kwargs):
        if after_ack:
            return ResponseFrame.ok_response("upstream", {"runId": "old", "status": "in_flight"})
        ready.set()
        await asyncio.Event().wait()

    async def write(_text):
        ready.set()

    client.send_request = send
    websocket.send_text.side_effect = write
    run = await _start_resume(server, websocket, auth_gate_service)
    await asyncio.wait_for(ready.wait(), 1)
    run.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run.task
    await asyncio.sleep(0)
    frames = [json.loads(call.args[0]) for call in websocket.send_text.await_args_list]
    assert frames[0]["type"] == "res"
    assert frames[0]["ok"] is after_ack
    if after_ack:
        assert frames[-1]["payload"]["state"] == "aborted"
        assert frames[-1]["payload"]["runId"] == "old"
    else:
        assert len(frames) == 1
    assert run.state == "aborted"
    assert run.finished_at is not None
    assert not any(client._event_listeners.values())
    fake_engine.on_connection_close.assert_awaited_once()


@pytest.mark.parametrize("engine", ["claude_code", "hermes", "deepseek_harness", "aicoding", "teclaw"])
async def test_other_engines_keep_immediate_ack(server, fake_engine, auth_gate_service, engine):
    EngineManager.get_instance()._engine = engine
    server._subscribe_conn_to_session = AsyncMock(return_value=False)
    server._stream_chat_events = AsyncMock()
    auth_gate_service.enabled = False
    params = {"sessionKey": "sk", "message": "hello"}
    response = await server._handle_chat_send(
        SimpleNamespace(send_text=AsyncMock()), "conn-1", _req("chat.send", params), params,
        auth_gate_service=auth_gate_service,
    )
    assert response.payload == {"accepted": True}
    assert not server._ack_chat_tasks
    await asyncio.sleep(0)
    server._stream_chat_events.assert_awaited_once()
    assert "on_ack" not in server._stream_chat_events.call_args.kwargs
