"""Unit tests for the OpenClaw chat ACL adapter.

Drives `OpenClawChatAdapter` against a fake `OpenClawChatPort` (async-generator
for chat_stream; canned dict for chat_abort). Tests assert:

1. Frames pass through + observer.observe called per frame + finalize called
   once on clean completion.
2. finalize NOT called when the port raises mid-stream (error frame yielded).
3. Missing sessionId raises ValueError.
4. Abort success builds ChatAbortResult with emit_events.
5. Abort failure (success=False) → ok=False.

IntentEvalObserver is monkeypatched in the adapter module so no real
Langfuse / intent_eval side-effects fire.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

import engine.community.core.adapters.openclaw.chat as chat_mod
from engine.community.core.adapters.openclaw.chat import OpenClawChatAdapter
from engine.community.core.chat.models import ChatAbortRequest, ChatAbortResult, ChatRequest
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import EventFrame


# ── helpers ───────────────────────────────────────────────────────────────────


@dataclass
class _FakeAuth:
    token: str | None = None


def _auth(token: str | None = "tok") -> AuthContext:
    return _FakeAuth(token=token)  # type: ignore[return-value]


def _request(session_id: str = "sk-1", query: str = "hello") -> ChatRequest:
    return ChatRequest(
        userId="u1",
        agentId="a1",
        query=query,
        sessionId=session_id,
    )


def _frame(state: str = "delta", text: str = "hi") -> EventFrame:
    return EventFrame(event="agent", payload={"state": state, "text": text})


async def _collect(gen) -> list[EventFrame]:
    """Drain an async generator into a list."""
    frames: list[EventFrame] = []
    async for item in gen:
        frames.append(item)
    return frames


# ── fake port ─────────────────────────────────────────────────────────────────


class _FakeChatPort:
    """Fake `OpenClawChatPort`."""

    def __init__(
        self,
        frames: list[EventFrame] | None = None,
        abort_result: dict[str, Any] | None = None,
        raise_on_stream: Exception | None = None,
    ) -> None:
        self._frames = frames if frames is not None else [
            _frame("delta", "hi"),
            _frame("final", "done"),
        ]
        self._abort_result = abort_result or {"success": True, "payload": {"runId": "r1", "aborted": True}}
        self._raise_on_stream = raise_on_stream

        self.stream_calls: list[dict] = []
        self.abort_calls: list[dict] = []
        self.inject_calls: list[dict] = []
        self.inject_result: dict[str, Any] = {
            "success": True,
            "payload": {"ok": True, "messageId": "m1"},
        }

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        idempotency_key: str | None = None,
        attachments: list | None = None,
        token: str | None = None,
    ):
        self.stream_calls.append({
            "session_key": session_key,
            "message": message,
            "timeout_ms": timeout_ms,
            "idempotency_key": idempotency_key,
            "attachments": attachments,
            "token": token,
        })
        if self._raise_on_stream is not None:
            raise self._raise_on_stream
        for frame in self._frames:
            yield frame

    async def chat_abort(self, session_key: str, run_id: str, token: str | None = None) -> dict:
        self.abort_calls.append({
            "session_key": session_key,
            "run_id": run_id,
            "token": token,
        })
        return self._abort_result

    async def chat_inject(
        self,
        session_key: str,
        message: str,
        label: str | None = None,
        token: str | None = None,
    ) -> dict:
        self.inject_calls.append({
            "session_key": session_key,
            "message": message,
            "label": label,
            "token": token,
        })
        return self.inject_result


# ── observer mock ─────────────────────────────────────────────────────────────


class _FakeObserver:
    """Fake IntentEvalObserver that records calls."""

    def __init__(self, *args, **kwargs) -> None:
        self.observed: list[EventFrame] = []
        self.finalize_count = 0

    def observe(self, frame: EventFrame) -> None:
        self.observed.append(frame)

    def finalize(self) -> None:
        self.finalize_count += 1


@pytest.fixture
def fake_observer(monkeypatch) -> _FakeObserver:
    """Monkeypatch IntentEvalObserver in the adapter module."""
    obs = _FakeObserver()

    def _ctor(*args, **kwargs) -> _FakeObserver:
        return obs

    monkeypatch.setattr(chat_mod, "IntentEvalObserver", _ctor)
    return obs


# ── test: missing sessionId ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_raises_if_session_id_missing(fake_observer):
    port = _FakeChatPort()
    adapter = OpenClawChatAdapter(port)
    req = ChatRequest(userId="u1", agentId="a1", query="hi", sessionId=None)
    with pytest.raises(ValueError, match="sessionId"):
        async for _ in adapter.stream(req):
            pass


# ── test: frames pass through + observer + finalize ──────────────────────────


@pytest.mark.asyncio
async def test_stream_passes_frames_and_calls_observe_and_finalize(fake_observer):
    frames = [_frame("delta", "part"), _frame("final", "all")]
    port = _FakeChatPort(frames=frames)
    adapter = OpenClawChatAdapter(port)

    collected = await _collect(adapter.stream(_request(), auth=_auth("t1")))

    # All frames yielded
    assert collected == frames
    # observer.observe called once per frame
    assert fake_observer.observed == frames
    # finalize called exactly once on clean completion
    assert fake_observer.finalize_count == 1
    # port received correct token
    assert port.stream_calls[0]["token"] == "t1"


@pytest.mark.asyncio
async def test_stream_passes_session_key_to_port(fake_observer):
    port = _FakeChatPort(frames=[_frame("final")])
    adapter = OpenClawChatAdapter(port)
    await _collect(adapter.stream(_request(session_id="user:1:session:2:agent:3")))
    assert port.stream_calls[0]["session_key"] == "user:1:session:2:agent:3"


# ── test: finalize NOT called on exception ────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_no_finalize_on_connection_error(fake_observer):
    port = _FakeChatPort(raise_on_stream=ConnectionError("boom"))
    adapter = OpenClawChatAdapter(port)

    collected = await _collect(adapter.stream(_request()))

    # One error frame yielded
    assert len(collected) == 1
    assert collected[0].event == "error"
    assert "boom" in collected[0].payload.get("errorMessage", "")
    # finalize MUST NOT be called
    assert fake_observer.finalize_count == 0


@pytest.mark.asyncio
async def test_stream_no_finalize_on_generic_exception(fake_observer):
    port = _FakeChatPort(raise_on_stream=RuntimeError("oops"))
    adapter = OpenClawChatAdapter(port)

    collected = await _collect(adapter.stream(_request()))

    assert len(collected) == 1
    assert collected[0].event == "error"
    assert fake_observer.finalize_count == 0


@pytest.mark.asyncio
async def test_stream_error_frame_has_session_key(fake_observer):
    port = _FakeChatPort(raise_on_stream=ConnectionError("no conn"))
    adapter = OpenClawChatAdapter(port)
    collected = await _collect(adapter.stream(_request(session_id="sk-err")))
    assert collected[0].payload["sessionKey"] == "sk-err"


# ── test: extraParams parsing ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_extracts_idempotency_key_from_extra_params(fake_observer):
    port = _FakeChatPort(frames=[_frame("final")])
    adapter = OpenClawChatAdapter(port)
    req = ChatRequest(
        userId="u",
        agentId="a",
        query="q",
        sessionId="sk",
        extraParams={"idempotencyKey": "  idem-1  "},
    )
    await _collect(adapter.stream(req))
    assert port.stream_calls[0]["idempotency_key"] == "idem-1"


@pytest.mark.asyncio
async def test_stream_extracts_attachments_from_extra_params(fake_observer):
    port = _FakeChatPort(frames=[_frame("final")])
    adapter = OpenClawChatAdapter(port)
    attach = [{"type": "file", "content": "data"}]
    req = ChatRequest(
        userId="u",
        agentId="a",
        query="q",
        sessionId="sk",
        extraParams={"attachments": attach},
    )
    await _collect(adapter.stream(req))
    assert port.stream_calls[0]["attachments"] == attach


# ── test: abort success ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abort_success_builds_result_with_emit_events():
    port = _FakeChatPort(
        abort_result={"success": True, "payload": {"runId": "run-123", "aborted": True}}
    )
    adapter = OpenClawChatAdapter(port)
    req = ChatAbortRequest(session_key="sk", run_id="run-123")
    result = await adapter.abort(req, auth=_auth("tok"))

    assert isinstance(result, ChatAbortResult)
    assert result.ok is True
    assert result.aborted is True
    assert result.run_id == "run-123"
    assert len(result.emit_events) == 1
    ev = result.emit_events[0]
    assert ev.event == "chat"
    assert ev.payload["state"] == "aborted"
    assert ev.payload["stopReason"] == "rpc"
    assert ev.payload["sessionKey"] == "sk"
    assert port.abort_calls[0]["token"] == "tok"


@pytest.mark.asyncio
async def test_abort_success_no_aborted_flag_gives_empty_emit_events():
    port = _FakeChatPort(
        abort_result={"success": True, "payload": {"runId": "r1", "aborted": False}}
    )
    adapter = OpenClawChatAdapter(port)
    result = await adapter.abort(ChatAbortRequest(session_key="sk", run_id="r1"))
    assert result.ok is True
    assert result.aborted is False
    assert result.emit_events == []


# ── test: abort failure ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abort_failure_returns_ok_false():
    port = _FakeChatPort(
        abort_result={
            "success": False,
            "error": {"code": "NOT_FOUND", "message": "run not found"},
        }
    )
    adapter = OpenClawChatAdapter(port)
    result = await adapter.abort(ChatAbortRequest(session_key="sk", run_id="r1"))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "NOT_FOUND"
    assert result.error.message == "run not found"
    assert result.emit_events == []


@pytest.mark.asyncio
async def test_abort_failure_with_no_error_dict_uses_unknown():
    port = _FakeChatPort(abort_result={"success": False})
    adapter = OpenClawChatAdapter(port)
    result = await adapter.abort(ChatAbortRequest(session_key="sk", run_id="r1"))
    assert result.ok is False
    assert result.error.code == "UNKNOWN"


@pytest.mark.asyncio
async def test_abort_passes_none_token_when_no_auth():
    port = _FakeChatPort()
    adapter = OpenClawChatAdapter(port)
    await adapter.abort(ChatAbortRequest(session_key="sk", run_id="r1"), auth=None)
    assert port.abort_calls[0]["token"] is None


@pytest.mark.asyncio
async def test_inject_calls_port_and_returns_payload():
    port = _FakeChatPort()
    adapter = OpenClawChatAdapter(port)

    result = await adapter.inject("sk", "hello", label="BCS", auth=_auth("tok"))

    assert result == {"ok": True, "payload": {"ok": True, "messageId": "m1"}}
    assert port.inject_calls == [
        {
            "session_key": "sk",
            "message": "hello",
            "label": "BCS",
            "token": "tok",
        }
    ]


@pytest.mark.asyncio
async def test_inject_returns_error_when_port_fails():
    port = _FakeChatPort()
    port.inject_result = {"success": False, "error": {"code": "E", "message": "nope"}}
    adapter = OpenClawChatAdapter(port)

    result = await adapter.inject("sk", "hello")

    assert result == {"ok": False, "error": {"code": "E", "message": "nope"}}


@pytest.mark.asyncio
async def test_inject_failure_without_error_uses_fallback():
    port = _FakeChatPort()
    port.inject_result = {"success": False}
    adapter = OpenClawChatAdapter(port)

    result = await adapter.inject("sk", "hello")

    assert result == {
        "ok": False,
        "error": {"code": "UNKNOWN", "message": "chat.inject failed"},
    }
