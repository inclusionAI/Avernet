"""Unit tests for ClaudeCodeRelayClient in _base.py.

Uses a fake WebSocket to exercise connect/disconnect, the recv loop,
frame dispatch, request/response correlation, and chat_stream — without
any real network or a running relay.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from engine.community.kernel.frames import EventFrame, ResponseFrame
from engine.community.openclaw.protocol import HelloOk
from engine.community.plugins.claude_code import _base
from engine.community.plugins.claude_code._base import (
    ClaudeCodePortBase,
    ClaudeCodeRelayClient,
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _hello_payload() -> dict[str, Any]:
    return {
        "type": "hello-ok",
        "protocol": 3,
        "server": {"version": "1.0.0", "connId": "c-1"},
        "features": {"methods": ["connect", "chat.send"], "events": ["agent"]},
        "snapshot": {},
        "policy": {"maxPayload": 1024, "maxBufferedBytes": 1024, "tickIntervalMs": 1000},
    }


def _challenge_frame() -> dict[str, Any]:
    return {"type": "event", "event": "connect.challenge", "payload": {}}


def _ok_res(rid: str, payload: Any = None) -> dict[str, Any]:
    d = {"type": "res", "id": rid, "ok": True}
    if payload is not None:
        d["payload"] = payload
    return d


def _err_res(rid: str, message: str = "nope", code: str = "E_FAIL") -> dict[str, Any]:
    return {"type": "res", "id": rid, "ok": False, "error": {"code": code, "message": message}}


class _FakeWS:
    """Fake websockets connection.

    Frames flow through an asyncio.Queue so recv()/__aiter__ block when empty
    (matching real socket semantics). Tests push additional frames via push()
    after connect; close() unblocks consumers.
    """

    def __init__(self, frames: list[dict] | None = None) -> None:
        self._q: asyncio.Queue[str] = asyncio.Queue()
        for f in (frames or []):
            self._q.put_nowait(json.dumps(f) if isinstance(f, dict) else f)
        self.sent: list[str] = []
        self.closed = False
        self._closed = False

    def push(self, frame: dict | str) -> None:
        self._q.put_nowait(json.dumps(frame) if isinstance(frame, dict) else frame)

    async def __aiter__(self):
        while True:
            if self._closed:
                return
            try:
                frame = await asyncio.wait_for(self._q.get(), timeout=0.02)
            except asyncio.TimeoutError:
                if self._closed:
                    return
                continue
            yield frame

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True
        self._closed = True

    async def recv(self) -> str:
        # Used by connect() for the initial handshake frames.
        return await self._q.get()


def _patch_ws(monkeypatch, fake_ws: _FakeWS, *, connect_raises: Exception | None = None) -> None:
    """Patch websockets.connect in _base to return fake_ws."""
    async def _fake_connect(*args, **kwargs):
        if connect_raises is not None:
            raise connect_raises
        return fake_ws
    monkeypatch.setattr(_base.websockets, "connect", _fake_connect)
    monkeypatch.setattr(_base.asyncio, "wait_for", _real_wait_for_except_connect_check())


def _real_wait_for_except_connect_check():
    """Return real asyncio.wait_for; connect() timeout path tested separately."""
    return asyncio.wait_for


def _patch_connect(monkeypatch, fake_ws: _FakeWS, *, raises: Exception | None = None) -> None:
    async def _fake_connect(*args, **kwargs):
        if raises is not None:
            raise raises
        return fake_ws
    monkeypatch.setattr(_base.websockets, "connect", _fake_connect)


# ── connect ─────────────────────────────────────────────────────────────────


async def test_connect_success(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)

    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    hello = await client.connect()

    assert client.connected is True
    assert isinstance(hello, HelloOk)
    assert hello.protocol == 3
    assert client.hello is hello
    # A connect request was sent.
    assert any(
        json.loads(s).get("method") == "connect" for s in ws.sent
    )
    # recv loop task was spawned.
    assert client._recv_task is not None
    await client.disconnect()


async def test_connect_already_connected_with_hello(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    h1 = await client.connect()
    h2 = await client.connect()  # second call returns cached hello, no new ws
    assert h1 is h2
    await client.disconnect()


async def test_connect_timeout(monkeypatch):
    async def _slow(*a, **kw):
        await asyncio.sleep(5)
        return _FakeWS()
    monkeypatch.setattr(_base.websockets, "connect", _slow)
    client = ClaudeCodeRelayClient(connection_timeout=0.05)
    with pytest.raises(ConnectionError, match="timeout"):
        await client.connect()
    assert client.connected is False


async def test_connect_other_exception(monkeypatch):
    _patch_connect(monkeypatch, _FakeWS(), raises=OSError("refused"))
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    with pytest.raises(ConnectionError, match="connect failed"):
        await client.connect()


async def test_connect_rejected_non_ok(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _err_res("1", "go away")])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    with pytest.raises(ConnectionError, match="rejected"):
        await client.connect()
    assert ws.closed is True


async def test_connect_challenge_timeout(monkeypatch):
    # recv() returns nothing quickly via a ws whose recv hangs.
    ws = _FakeWS(frames=[])  # no challenge frame
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=0.05)
    with pytest.raises(ConnectionError, match="challenge timeout"):
        await client.connect()
    assert ws.closed is True


# ── disconnect ──────────────────────────────────────────────────────────────


async def test_disconnect_closes_and_marks_disconnected(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()
    await client.disconnect()
    assert client.connected is False
    assert ws.closed is True
    assert client._recv_task is None


async def test_disconnect_no_ws_is_noop():
    client = ClaudeCodeRelayClient()
    # Should not raise.
    await client.disconnect()
    assert client.connected is False


# ── _fail_all_pending ───────────────────────────────────────────────────────


async def test_fail_all_pending_rejects_futures():
    client = ClaudeCodeRelayClient()
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending["abc"] = fut
    client._fail_all_pending(ConnectionError("lost"))
    assert fut.done()
    with pytest.raises(ConnectionError):
        fut.result()
    assert client._pending == {}


async def test_fail_all_pending_unblocks_stream_queues():
    client = ClaudeCodeRelayClient()
    q: asyncio.Queue = asyncio.Queue()
    client._stream_queues["s1"] = q
    client._fail_all_pending(ConnectionError("lost"))
    event = await q.get()
    assert event.event == "error"
    assert event.payload["state"] == "error"


# ── send_request ────────────────────────────────────────────────────────────


async def test_send_request_not_connected_raises():
    client = ClaudeCodeRelayClient()
    with pytest.raises(ConnectionError, match="not connected"):
        await client.send_request("foo")


async def test_send_request_dispatched_response(monkeypatch):
    # Pre-fill handshake + the response frame will be pushed after request.
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    # After connect, the recv loop is consuming frames via __aiter__.
    # Push a response for the next request id (which will be "2").
    ws.push(_ok_res("2", {"ack": True}))

    resp = await client.send_request("ping", {"x": 1}, timeout=2.0)
    assert isinstance(resp, ResponseFrame)
    assert resp.ok is True
    assert resp.payload == {"ack": True}
    await client.disconnect()


async def test_send_request_error_response(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    ws.push(_err_res("2", "bad"))
    resp = await client.send_request("ping", timeout=2.0)
    assert resp.ok is False
    assert resp.error is not None
    await client.disconnect()


async def test_send_request_timeout_pops_pending(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    with pytest.raises(asyncio.TimeoutError):
        await client.send_request("hang", timeout=0.05)
    # pending entry removed by finally.
    assert client._pending == {}
    await client.disconnect()


# ── send_request_with_events ────────────────────────────────────────────────


async def test_send_request_with_events_collects(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    # The recv loop will pick up these frames; the res correlates with the
    # request id "2", and the event is routed via the sessionKey queue.
    ws.push(_ok_res("2", {"accepted": True}))
    ws.push({
        "type": "event",
        "event": "agent",
        "payload": {"sessionKey": "sk-1", "delta": "hi"},
    })

    resp, events = await client.send_request_with_events(
        "chat.send",
        {"sessionKey": "sk-1"},
        event_names=["agent"],
        session_key="sk-1",
        response_timeout=2.0,
    )
    assert resp.ok is True
    assert len(events) >= 1
    await client.disconnect()


# ── send_request_with_id ────────────────────────────────────────────────────


async def test_send_request_with_id(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    ws.push(_ok_res("2", {"ok": True}))
    resp = await client.send_request_with_id(
        "req-xyz", "chat.send", {"k": "v"}, timeout=2.0
    )
    assert resp.ok is True
    await client.disconnect()


# ── chat_stream ─────────────────────────────────────────────────────────────


async def test_chat_stream_yields_until_final(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    # res for chat.send (id "2")
    ws.push(_ok_res("2", {"accepted": True}))
    ws.push({"type": "event", "event": "agent",
             "payload": {"sessionKey": "s-stream", "state": "delta", "text": "a"}})
    ws.push({"type": "event", "event": "agent",
             "payload": {"sessionKey": "s-stream", "state": "final", "text": "done"}})

    out: list[dict] = []
    async for payload in client.chat_stream("s-stream", "hello", timeout_ms=100):
        out.append(payload)

    assert len(out) == 2
    assert out[0]["state"] == "delta"
    assert out[1]["state"] == "final"
    assert out[1]["_source_event"] == "agent"
    # queue cleaned up
    assert "s-stream" not in client._stream_queues
    await client.disconnect()


async def test_chat_stream_not_connected_raises():
    client = ClaudeCodeRelayClient()
    with pytest.raises(ConnectionError, match="not connected"):
        async for _ in client.chat_stream("s", "hi"):
            pass


async def test_chat_stream_duplicate_session_raises(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()
    # Pre-register the sessionKey to simulate an active stream.
    client._stream_queues["dup"] = asyncio.Queue()
    with pytest.raises(RuntimeError, match="Another chat_stream"):
        async for _ in client.chat_stream("dup", "hi"):
            pass
    await client.disconnect()


# ── on_event / off_event ────────────────────────────────────────────────────


async def test_on_event_listener_dispatched(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    received: list[EventFrame] = []
    client.on_event("agent", received.append)

    ws.push({"type": "event", "event": "agent", "payload": {"hi": 1}})
    # Give the recv loop a tick to process.
    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0].event == "agent"

    # off_event removes the listener.
    client.off_event("agent", received.append)
    ws.push({"type": "event", "event": "agent", "payload": {"hi": 2}})
    await asyncio.sleep(0.05)
    assert len(received) == 1  # unchanged
    await client.disconnect()


async def test_off_event_unknown_listener_is_noop(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    def lst(e): ...
    # Removing a never-registered listener should not raise.
    client.off_event("agent", lst)
    await client.disconnect()


# ── recv loop robustness ────────────────────────────────────────────────────


async def test_recv_loop_drops_non_json_frame(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()
    # Push non-json into the recv loop (after handshake).
    ws.push("__bad json__")
    ws.push({"type": "event", "event": "agent", "payload": {}})
    await asyncio.sleep(0.1)
    assert client.connected is True  # loop still alive after dropping bad frame
    await client.disconnect()


async def test_recv_loop_socket_death_fails_pending(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    await client.connect()

    # Register a pending request future with no matching response.
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    client._pending["never"] = fut

    # Force the recv loop to exit by closing the ws (frames exhausted).
    # The __aiter__ will complete, triggering the finally block.
    await asyncio.sleep(0.1)

    # Manually close ws to end iteration; the recv loop's finally should
    # fail pending and mark disconnected.
    await ws.close()
    # Give the loop a moment to wind down.
    await asyncio.sleep(0.1)

    # Future should be failed with ConnectionError.
    assert fut.done()
    with pytest.raises(ConnectionError):
        fut.result()
    assert client.connected is False


# ── ClaudeCodePortBase ──────────────────────────────────────────────────────


async def test_port_base_relay_lazy_connect(monkeypatch):
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    client = ClaudeCodeRelayClient(connection_timeout=2.0)
    base = ClaudeCodePortBase(client=client)
    got = await base._relay()
    assert got is client
    assert client.connected is True
    await client.disconnect()


async def test_port_base_creates_default_client(monkeypatch):
    # Don't inject a client; _relay() should construct one.
    ws = _FakeWS(frames=[_challenge_frame(), _ok_res("1", _hello_payload())])
    _patch_connect(monkeypatch, ws)
    base = ClaudeCodePortBase()
    got = await base._relay()
    assert isinstance(got, ClaudeCodeRelayClient)
    assert got.connected is True
    await got.disconnect()