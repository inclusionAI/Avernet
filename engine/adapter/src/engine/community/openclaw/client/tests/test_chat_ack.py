from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from engine.community.openclaw.client import gateway_client as module
from engine.community.openclaw.client.tests.test_gateway_client import (
    _fire_compaction_end, _fire_event, _fire_lifecycle_end, _fire_text_chat, _make_client,
)
from engine.community.openclaw.protocol import ErrorShape, ResponseFrame
from engine.community.plugin_api.openclaw.chat import OpenClawChatAck


@pytest.mark.parametrize("ok,status,cached", [
    (True, "started", False), (True, "in_flight", True), (True, "ok", True),
    (True, "error", None), (True, None, None), (True, "future", None),
    (False, "error", None), (False, "in_flight", None),
])
def test_ack_preserves_upstream_fields_and_infers_only_known_states(ok, status, cached):
    error = None if ok else ErrorShape("UNAVAILABLE", "failed", retryable=True)
    payload = {"runId": "actual", "summary": "details"}
    if status is not None:
        payload["status"] = status
    response = OpenClawChatAck(ok, payload, error).response("downstream-id")
    assert response.id == "downstream-id"
    assert response.ok is ok
    assert response.error is error
    assert response.payload == {**payload, "accepted": ok, "cached": cached}


@pytest.mark.parametrize("status", ["started", "in_flight", "future", None])
async def test_ack_precedes_buffered_events_and_uses_actual_run(status):
    client = _make_client()
    seen = []

    async def send(*args, **kwargs):
        _fire_event(client, "chat", {"runId": "foreign", "state": "final"})
        _fire_event(client, "approval.requested", {"runId": "actual", "sessionKey": "sk"})
        _fire_event(client, "approval.resolved", {"runId": "actual", "sessionKey": "sk"})
        _fire_event(client, "chat", {
            "runId": "actual", "sessionKey": "sk", "state": "final",
            "message": {"content": [{"type": "text", "text": "answer"}]},
        })
        return ResponseFrame.ok_response("upstream", {"runId": "actual", "status": status})

    async def ack(response):
        seen.append("ack")
        assert response.payload["runId"] == "actual"
        await asyncio.sleep(0)

    client.send_request = send
    async for event in client.chat_stream("sk", "hello", idempotency_key="new", on_ack=ack):
        seen.append(event["state"])
    assert seen == ["ack", "approval_requested", "approval_resolved", "final"]
    assert not any(client._event_listeners.values())


@pytest.mark.parametrize("ok,status", [(True, "ok"), (False, "error")])
async def test_terminal_ack_does_not_wait_for_another_final(ok, status):
    client = _make_client()
    response = ResponseFrame(
        id="upstream", ok=ok, payload={"runId": "old", "status": status},
        error=None if ok else ErrorShape("UNAVAILABLE", "failed"),
    )
    client.send_request = AsyncMock(return_value=response)
    ack = AsyncMock()
    async def collect():
        return [event async for event in client.chat_stream("sk", "hello", on_ack=ack)]
    assert await asyncio.wait_for(collect(), 1) == []
    ack.assert_awaited_once_with(response)
    assert not any(client._event_listeners.values())


async def test_pre_ack_overflow_fails_and_releases_listeners(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(module, "_MAX_PRE_ACK_BYTES", 1)
    async def send(*args, **kwargs):
        _fire_event(client, "chat", {"runId": "other", "state": "delta"})
        _fire_event(client, "chat", {"runId": "other", "state": "delta"})
        return ResponseFrame.ok_response("id", {"runId": "actual", "status": "started"})
    client.send_request = send
    ack = AsyncMock()
    with pytest.raises(BufferError, match="buffer exceeded"):
        await anext(client.chat_stream("sk", "hello", on_ack=ack))
    ack.assert_not_awaited()
    assert not any(client._event_listeners.values())


async def test_overflow_during_downstream_ack_write_releases_listeners(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(module, "_MAX_PRE_ACK_BYTES", 1)
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response(
        "id", {"runId": "actual", "status": "started"},
    ))
    async def ack(response):
        _fire_event(client, "chat", {"runId": "actual", "state": "delta"})
    with pytest.raises(BufferError, match="buffer exceeded"):
        await anext(client.chat_stream("sk", "hello", on_ack=ack))
    assert not any(client._event_listeners.values())


@pytest.mark.parametrize("failure", [TimeoutError("ACK timeout"), ConnectionError("closed")])
async def test_rpc_failure_releases_listeners(failure):
    client = _make_client()
    client.send_request = AsyncMock(side_effect=failure)
    with pytest.raises(type(failure)):
        await anext(client.chat_stream("sk", "hello", on_ack=AsyncMock()))
    assert not any(client._event_listeners.values())


async def test_cancel_while_waiting_for_ack_releases_listeners():
    client = _make_client()
    started = asyncio.Event()
    async def send(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    client.send_request = send
    task = asyncio.create_task(anext(client.chat_stream("sk", "hello", on_ack=AsyncMock())))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(client._event_listeners.values())


async def test_downstream_ack_failure_releases_listeners():
    client = _make_client()
    client.send_request = AsyncMock(return_value=ResponseFrame.ok_response("id", {"status": "started"}))
    with pytest.raises(ConnectionError):
        await anext(client.chat_stream("sk", "hello", on_ack=AsyncMock(side_effect=ConnectionError())))
    assert not any(client._event_listeners.values())


@pytest.mark.parametrize("with_ack", [False, True])
@pytest.mark.parametrize("event_name", ["chat", "agent", "approval.requested", "approval.resolved"])
async def test_foreign_session_does_not_consume_pending_ack_budget(monkeypatch, with_ack, event_name):
    client = _make_client()
    monkeypatch.setattr(module, "_MAX_PRE_ACK_BYTES", 1024)

    async def send(*args, **kwargs):
        _fire_event(client, event_name, {
            "runId": "foreign", "sessionKey": "agent:other:sk",
            "state": "delta", "text": "x" * 4096,
        })
        _fire_event(client, "chat", {
            "runId": "mine", "sessionKey": "sk", "state": "final", "text": "answer",
        })
        return ResponseFrame.ok_response("upstream", {"runId": "mine", "status": "started"})

    client.send_request = send
    ack = AsyncMock()
    kwargs = {"on_ack": ack} if with_ack else {}
    events = [event async for event in client.chat_stream("sk", "hello", idempotency_key="mine", **kwargs)]
    assert [event["state"] for event in events] == ["final"]
    assert ack.await_count == int(with_ack)
    assert not any(client._event_listeners.values())


@pytest.mark.parametrize("event_session", [None, "sk", "agent:main:SK"])
async def test_reused_run_early_event_survives_session_prefilter(event_session):
    client = _make_client()

    async def send(*args, **kwargs):
        payload = {"runId": "old", "state": "final", "text": "answer"}
        if event_session is not None:
            payload["sessionKey"] = event_session
        _fire_event(client, "chat", payload)
        return ResponseFrame.ok_response("upstream", {"runId": "old", "status": "in_flight"})

    client.send_request = send
    ack = AsyncMock()
    events = [event async for event in client.chat_stream("sk", "hello", idempotency_key="new", on_ack=ack)]
    assert events[0]["runId"] == "old"
    ack.assert_awaited_once()


async def test_legacy_stream_does_not_acquire_ack_buffer_limit(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(module, "_MAX_PRE_ACK_BYTES", 1)

    async def send(*args, **kwargs):
        _fire_event(client, "chat", {"runId": "mine", "state": "final", "text": "answer"})
        return ResponseFrame.ok_response("upstream", {"runId": "mine", "status": "started"})

    client.send_request = send
    events = [event async for event in client.chat_stream("sk", "hello", idempotency_key="mine")]
    assert events[0]["text"] == "answer"


async def test_ack_buffer_preserves_compaction_terminal_suppression(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    client = _make_client()
    order = []

    async def send(*args, **kwargs):
        _fire_text_chat(client, text="Before ", state="delta")
        _fire_compaction_end(client)
        _fire_lifecycle_end(client)
        _fire_text_chat(client, text="Before ", state="final")
        _fire_text_chat(client, text="after", state="delta")
        _fire_lifecycle_end(client)
        _fire_text_chat(client, text="after", state="final")
        return ResponseFrame.ok_response("upstream", {"runId": "run-1", "status": "in_flight"})

    async def ack(response):
        order.append("ack")

    client.send_request = send
    async for event in client.chat_stream("sk-1", "hello", idempotency_key="new", on_ack=ack):
        if event.get("state") in ("delta", "final"):
            order.append(event["state"])
    assert order == ["ack", "delta", "delta", "final"]
    assert not any(client._event_listeners.values())
