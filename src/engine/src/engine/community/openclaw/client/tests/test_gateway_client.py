from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import pytest

from engine.community.openclaw.client.gateway_client import OpenClawGatewayClient
from engine.community.openclaw.config import OpenClawConfig
from engine.community.openclaw.protocol import (
    ErrorCodes,
    EventFrame,
    OPENCLAW_GATEWAY_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    ResponseFrame,
)


def _make_client() -> OpenClawGatewayClient:
    client = OpenClawGatewayClient(config=OpenClawConfig(gateway_url="ws://127.0.0.1:0"))
    client._connected = True
    return client


def test_openclaw_gateway_protocol_version_matches_current_gateway():
    assert PROTOCOL_VERSION == 3
    assert OPENCLAW_GATEWAY_PROTOCOL_VERSION == 4


def _fire_event(
    client: OpenClawGatewayClient,
    event_name: str,
    payload: Dict[str, Any],
) -> None:
    frame = EventFrame(event=event_name, payload=payload)
    for listener in list(client._event_listeners.get(event_name, [])):
        listener(frame)


async def _collect(stream, limit: int = 10) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    async for event in stream:
        events.append(event)
        if len(events) >= limit:
            break
    return events


@pytest.mark.asyncio
async def test_chat_stream_drops_terminal_from_different_run_same_session(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()
    sent_params: Dict[str, Any] = {}

    async def fake_send_request(
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        sent_params.update(params or {})
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "old-run",
                "state": "final",
            },
        )
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "current-run",
                "state": "delta",
                "text": "ok",
            },
        )
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "current-run",
                "state": "final",
            },
        )
        return ResponseFrame.ok_response("rid", {"runId": "current-run"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            idempotency_key="current-run",
        )
    )

    assert sent_params["idempotencyKey"] == "current-run"
    assert [event["runId"] for event in events] == ["current-run", "current-run"]
    assert [event["state"] for event in events] == ["delta", "final"]


@pytest.mark.asyncio
async def test_chat_stream_uses_expected_run_id_when_response_missing_run_id(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()

    async def fake_send_request(*_args, **_kwargs) -> ResponseFrame:
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "expected-run",
                "state": "final",
            },
        )
        return ResponseFrame.ok_response("rid", {"accepted": True})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            idempotency_key="expected-run",
        )
    )

    assert len(events) == 1
    assert events[0]["runId"] == "expected-run"
    assert events[0]["state"] == "final"


@pytest.mark.asyncio
async def test_chat_stream_sends_attachments(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()
    sent_params = {}
    attachments = [
        {
            "type": "file",
            "mimeType": "application/pdf",
            "fileName": "brief.pdf",
            "content": "YmFzZTY0",
        }
    ]

    async def fake_send_request(_method: str, params: dict, *_args, **_kwargs) -> ResponseFrame:
        sent_params.update(params)
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "expected-run",
                "state": "final",
            },
        )
        return ResponseFrame.ok_response("rid", {"accepted": True})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            idempotency_key="expected-run",
            attachments=attachments,
        )
    )

    assert sent_params["attachments"] == attachments
    assert len(events) == 1


@pytest.mark.asyncio
async def test_chat_stream_forwards_inject_final_without_terminating(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()

    async def fake_send_request(*_args, **_kwargs) -> ResponseFrame:
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "inject-1",
                "state": "final",
            },
        )
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "current-run",
                "state": "final",
            },
        )
        return ResponseFrame.ok_response("rid", {"runId": "current-run"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            idempotency_key="current-run",
        )
    )

    assert [event["runId"] for event in events] == ["inject-1", "current-run"]
    assert [event["state"] for event in events] == ["final", "final"]


@pytest.mark.asyncio
async def test_chat_stream_discards_early_final_when_followup_run_arrives(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "1")
    client = _make_client()

    async def fake_send_request(*_args, **_kwargs) -> ResponseFrame:
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "expected-run",
                "state": "final",
                "seq": 1,
            },
        )

        async def followup() -> None:
            await asyncio.sleep(0.01)
            _fire_event(
                client,
                "agent",
                {
                    "sessionKey": "sk-1",
                    "runId": "followup-run",
                    "stream": "lifecycle",
                    "data": {"phase": "start"},
                    "seq": 1,
                },
            )
            _fire_event(
                client,
                "chat",
                {
                    "sessionKey": "sk-1",
                    "runId": "followup-run",
                    "state": "delta",
                    "text": "real response",
                    "seq": 2,
                },
            )
            _fire_event(
                client,
                "chat",
                {
                    "sessionKey": "sk-1",
                    "runId": "followup-run",
                    "state": "final",
                    "seq": 3,
                },
            )

        asyncio.create_task(followup())
        return ResponseFrame.ok_response("rid", {"runId": "expected-run"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            idempotency_key="expected-run",
        )
    )

    assert [event["runId"] for event in events] == [
        "followup-run",
        "followup-run",
        "followup-run",
    ]
    assert [event.get("state") for event in events] == [None, "delta", "final"]


@pytest.mark.asyncio
async def test_chat_stream_accepts_immediate_followup_after_early_final(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "1")
    client = _make_client()

    async def fake_send_request(*_args, **_kwargs) -> ResponseFrame:
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "expected-run",
                "state": "final",
                "seq": 1,
            },
        )
        _fire_event(
            client,
            "agent",
            {
                "sessionKey": "sk-1",
                "runId": "followup-run",
                "stream": "lifecycle",
                "data": {"phase": "start"},
                "seq": 1,
            },
        )
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "followup-run",
                "state": "final",
                "seq": 2,
            },
        )
        return ResponseFrame.ok_response("rid", {"runId": "expected-run"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            idempotency_key="expected-run",
        )
    )

    assert [event["runId"] for event in events] == ["followup-run", "followup-run"]
    assert [event.get("state") for event in events] == [None, "final"]


@pytest.mark.asyncio
async def test_chat_stream_does_not_hold_final_with_message(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "1")
    client = _make_client()

    async def fake_send_request(*_args, **_kwargs) -> ResponseFrame:
        _fire_event(
            client,
            "chat",
            {
                "sessionKey": "sk-1",
                "runId": "expected-run",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "complete"}],
                },
            },
        )
        return ResponseFrame.ok_response("rid", {"runId": "expected-run"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    started = asyncio.get_running_loop().time()
    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            idempotency_key="expected-run",
        )
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert len(events) == 1
    assert events[0]["state"] == "final"
    assert elapsed < 0.5


# ─────────────────────────────────────────────────────────────────────────────
# chat_stream timeout diagnostic logging
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_stream_uses_twenty_minute_default_timeout(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()
    sent_params: Dict[str, Any] = {}
    observed_timeouts: list[float] = []

    async def fake_send_request(
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        sent_params.update(params or {})
        return ResponseFrame.ok_response("rid", {"runId": "run-1"})

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        observed_timeouts.append(timeout)
        raise asyncio.TimeoutError

    client.send_request = fake_send_request  # type: ignore[method-assign]
    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            idempotency_key="run-1",
        )
    )

    assert "timeoutMs" not in sent_params
    assert observed_timeouts == [20 * 60]
    assert events[0]["errorMessage"] == "Chat stream timeout"


@pytest.mark.asyncio
async def test_chat_stream_timeout_no_event_received(monkeypatch):
    """timeout reason = no_event_received_from_upstream：
    openclaw ack 了但始终不推 event。"""
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()

    async def fake_send_request(method, params=None, timeout=30.0):
        return ResponseFrame.ok_response("rid", {"runId": "run-1"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            timeout_ms=100,  # 100ms 快速超时
            idempotency_key="run-1",
        )
    )

    assert len(events) == 1
    assert events[0]["state"] == "error"
    assert events[0]["errorMessage"] == "Chat stream timeout"
    assert events[0]["errorCode"] == ErrorCodes.AGENT_TIMEOUT


@pytest.mark.asyncio
async def test_chat_stream_timeout_all_events_filtered(monkeypatch):
    """timeout reason = all_events_filtered_drop_count=N：
    收到了 event 但 runId 不匹配，全被 filter_drop。"""
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()

    async def fake_send_request(method, params=None, timeout=30.0):
        # 发一些 runId 不匹配的 event
        _fire_event(client, "chat", {
            "sessionKey": "sk-1",
            "runId": "wrong-run-id",
            "state": "delta",
            "text": "hello",
        })
        _fire_event(client, "chat", {
            "sessionKey": "sk-1",
            "runId": "wrong-run-id",
            "state": "delta",
            "text": "world",
        })
        return ResponseFrame.ok_response("rid", {"runId": "expected-run"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            timeout_ms=100,
            idempotency_key="expected-run",
        )
    )

    assert len(events) == 1
    assert events[0]["state"] == "error"
    assert events[0]["errorMessage"] == "Chat stream timeout"


@pytest.mark.asyncio
async def test_chat_stream_timeout_stream_stalled_mid_response(monkeypatch):
    """timeout reason = stream_stalled_mid_response：
    收到过正常 event 后断流了。"""
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()

    async def fake_send_request(method, params=None, timeout=30.0):
        # 发一个正常的非终态 event，然后就不再发了
        _fire_event(client, "chat", {
            "sessionKey": "sk-1",
            "runId": "run-1",
            "state": "delta",
            "text": "partial",
        })
        return ResponseFrame.ok_response("rid", {"runId": "run-1"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            timeout_ms=100,
            idempotency_key="run-1",
        )
    )

    # 收到了 delta + timeout error
    assert len(events) == 2
    assert events[0]["state"] == "delta"
    assert events[1]["state"] == "error"
    assert events[1]["errorMessage"] == "Chat stream timeout"


@pytest.mark.asyncio
async def test_chat_stream_timeout_ws_disconnected(monkeypatch):
    """timeout reason = ws_disconnected：
    timeout 时 WS 连接已断。"""
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()

    async def fake_send_request(method, params=None, timeout=30.0):
        # ack 后立即断开连接
        client._connected = False
        client._ws = None
        return ResponseFrame.ok_response("rid", {"runId": "run-1"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            timeout_ms=100,
            idempotency_key="run-1",
        )
    )

    assert len(events) == 1
    assert events[0]["state"] == "error"
    assert events[0]["errorMessage"] == "Chat stream timeout"


@pytest.mark.asyncio
async def test_chat_stream_timeout_waiting_subagent_final(monkeypatch):
    """timeout reason = waiting_subagent_final_pending_announces=1：
    subagent spawn 成功但 final 没来。"""
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()

    async def fake_send_request(method, params=None, timeout=30.0):
        # 模拟 sessions_spawn tool 成功 → pending_announces 递增
        _fire_event(client, "chat", {
            "sessionKey": "sk-1",
            "runId": "run-1",
            "state": "delta",
            "stream": "tool",
            "data": {"name": "sessions_spawn", "phase": "result", "isError": False},
        })
        # 然后不再发 final
        return ResponseFrame.ok_response("rid", {"runId": "run-1"})

    client.send_request = fake_send_request  # type: ignore[method-assign]

    events = await _collect(
        client.chat_stream(
            session_key="sk-1",
            message="hello",
            timeout_ms=100,
            idempotency_key="run-1",
        )
    )

    # 收到 delta(tool) + timeout error
    assert len(events) == 2
    assert events[0].get("stream") == "tool"
    assert events[1]["state"] == "error"
    assert events[1]["errorMessage"] == "Chat stream timeout"
