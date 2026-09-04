"""Integration tests for the active-run side channel wired into
`OpenClawChatAdapter.stream`.

Feeds controlled frame sequences through the adapter and asserts OpenClaw's own
`ActiveRunRegistry` reflects the lifecycle correctly:
  - register before the first upstream event and reconcile non-`inject-`
    runId frames
  - terminal on final/error/aborted
  - finally safety net for transport errors / disconnect
  - `inject-` frames are never tracked
  - multi-run / multi-session isolation
"""
from __future__ import annotations

from typing import Any

import pytest

import engine.community.core.adapters.openclaw.chat as chat_mod
from engine.community.core.adapters.openclaw.active_run_registry import ActiveRunRegistry
from engine.community.core.adapters.openclaw.chat import OpenClawChatAdapter
from engine.community.core.chat.models import ChatRequest
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import EventFrame


# Reuse the fake-port + helpers pattern from test_chat.py but keep this file
# self-contained so the active-session lifecycle is readable in isolation.
class _FakePort:
    def __init__(self, frames: list[EventFrame], raise_on_stream: Exception | None = None):
        self._frames = frames
        self._raise = raise_on_stream

    async def chat_stream(self, session_key, message, timeout_ms=None, idempotency_key=None, attachments=None, token=None):
        if self._raise is not None:
            raise self._raise
        for frame in self._frames:
            yield frame


class _FakeAuth:
    token: str | None = None


def _req(session_id: str = "session:a:user:u") -> ChatRequest:
    return ChatRequest(userId="u", agentId="", query="hi", sessionId=session_id)


def _frame(state: str, run_id: str, session_key: str = "session:a:user:u", event: str = "agent") -> EventFrame:
    return EventFrame(event=event, payload={"state": state, "runId": run_id, "sessionKey": session_key})


async def _drain(adapter: OpenClawChatAdapter, req: ChatRequest, auth: AuthContext | None = None) -> list[EventFrame]:
    out: list[EventFrame] = []
    async for f in adapter.stream(req, auth=auth):
        out.append(f)
    return out


@pytest.fixture(autouse=True)
def _no_intent_eval(monkeypatch):
    """Disable IntentEvalObserver side effects (mirrors test_chat.py)."""
    monkeypatch.setattr(chat_mod, "IntentEvalObserver", lambda *a, **k: _NoOpObserver())


class _NoOpObserver:
    def observe(self, frame: EventFrame) -> None:  # noqa: D401
        return None

    def finalize(self) -> None:
        return None


class TestRegisterAndTerminal:
    async def test_running_then_final_marks_completed(self):
        reg = ActiveRunRegistry()
        port = _FakePort([_frame("delta", "run-1"), _frame("final", "run-1")])
        adapter = OpenClawChatAdapter(port, active_run_registry=reg)  # type: ignore[arg-type]
        await _drain(adapter, _req())
        assert reg.snapshot() == []
        assert reg.all_runs()[0].state == "completed"

    async def test_error_frame_marks_failed(self):
        reg = ActiveRunRegistry()
        port = _FakePort([_frame("delta", "run-1"), _frame("error", "run-1")])
        adapter = OpenClawChatAdapter(port, active_run_registry=reg)  # type: ignore[arg-type]
        await _drain(adapter, _req())
        assert reg.all_runs()[0].state == "failed"
        assert reg.snapshot() == []

    async def test_aborted_frame_marks_aborted(self):
        reg = ActiveRunRegistry()
        port = _FakePort([_frame("delta", "run-1"), _frame("aborted", "run-1")])
        adapter = OpenClawChatAdapter(port, active_run_registry=reg)  # type: ignore[arg-type]
        await _drain(adapter, _req())
        assert reg.all_runs()[0].state == "aborted"

    async def test_mid_stream_active_visible(self):
        reg = ActiveRunRegistry()
        port = _FakePort([_frame("delta", "run-1"), _frame("delta", "run-1"), _frame("final", "run-1")])
        adapter = OpenClawChatAdapter(port, active_run_registry=reg)  # type: ignore[arg-type]
        gen = adapter.stream(_req())
        # Advance past the first frame but do NOT drain — the run should now be
        # registered and visibly active mid-stream.
        first = await gen.__anext__()
        assert first.payload["runId"] == "run-1"
        assert len(reg.snapshot()) == 1
        assert reg.all_runs()[0].state == "running"
        # Drain the rest; the final frame terminates the run.
        async for _ in gen:
            pass
        assert reg.snapshot() == []
        assert reg.all_runs()[0].state == "completed"


class TestRunRegistrationBeforeFirstEvent:
    async def test_run_is_active_before_upstream_emits_event(self):
        """A successful chat start must be visible before the first event.

        OpenClaw can acknowledge ``chat.send`` and then pause before emitting
        its first event. The active-run query must not report clear during that
        interval.
        """
        import asyncio

        reg = ActiveRunRegistry()
        started = asyncio.Event()
        release = asyncio.Event()

        class _AckedButSilentPort:
            async def chat_stream(self, *args, **kwargs):
                started.set()
                await release.wait()
                if False:
                    yield _frame("delta", "never")

        adapter = OpenClawChatAdapter(
            _AckedButSilentPort(), active_run_registry=reg
        )  # type: ignore[arg-type]
        task = asyncio.create_task(_drain(adapter, _req()))
        await started.wait()
        await asyncio.sleep(0)

        result = await reg.query()
        assert result.verdict == "active"
        assert result.count == 1

        release.set()
        await task
        assert reg.snapshot() == []


class TestSafetyNet:
    async def test_transport_exception_marks_failed(self):
        reg = ActiveRunRegistry()
        # First register the run via a delta frame, then the port raises on a
        # second frame (simulating a mid-stream transport failure).
        class _RaisePort:
            async def chat_stream(self, *a, **k):
                yield _frame("delta", "run-1")
                raise ConnectionError("upstream gone")

        adapter = OpenClawChatAdapter(_RaisePort(), active_run_registry=reg)  # type: ignore[arg-type]
        frames = await _drain(adapter, _req())
        assert any(f.payload.get("state") == "error" for f in frames)
        assert reg.all_runs()[0].state == "failed"
        assert reg.snapshot() == []

    async def test_no_registry_is_no_op(self):
        # Adapter constructed without a registry must behave as before.
        port = _FakePort([_frame("delta", "run-1"), _frame("final", "run-1")])
        adapter = OpenClawChatAdapter(port)  # type: ignore[arg-type]
        frames = await _drain(adapter, _req())
        assert len(frames) == 2

    async def test_clean_completion_without_terminal_frame_marks_completed(self):
        reg = ActiveRunRegistry()
        # Stream drains with only a delta (no explicit final). The finally
        # safety net must move it to `completed`, not leave it lingering.
        port = _FakePort([_frame("delta", "run-1")])
        adapter = OpenClawChatAdapter(port, active_run_registry=reg)  # type: ignore[arg-type]
        await _drain(adapter, _req())
        assert reg.all_runs()[0].state == "completed"
        assert reg.snapshot() == []


class TestInjectSkipped:
    async def test_inject_run_ids_not_tracked(self):
        reg = ActiveRunRegistry()
        inject_frame = EventFrame(
            event="chat", payload={"state": "final", "runId": "inject-xyz", "sessionKey": "session:a:user:u"}
        )
        port = _FakePort([inject_frame])
        adapter = OpenClawChatAdapter(port, active_run_registry=reg)  # type: ignore[arg-type]
        await _drain(adapter, _req())
        assert reg.all_runs() == []


class TestIsolation:
    async def test_multi_run_per_stream_does_not_strand(self):
        # A single stream surfaces two distinct run ids (e.g. a gateway
        # re-issue mid-stream) and drains cleanly without an explicit terminal
        # frame. The finally safety net must mark BOTH runs terminal so neither
        # lingers as a false "active".
        reg = ActiveRunRegistry()

        class _TwoRunPort:
            async def chat_stream(self, *a, **k):
                yield _frame("delta", "run-1", "session:a:user:u")
                yield _frame("delta", "run-2", "session:a:user:u")

        adapter = OpenClawChatAdapter(_TwoRunPort(), active_run_registry=reg)  # type: ignore[arg-type]
        await _drain(adapter, _req())
        runs = {r.run_id: r for r in reg.all_runs()}
        assert set(runs) == {"run-1", "run-2"}
        # Neither remains running — both reached a terminal state via finally.
        assert reg.snapshot() == []
        assert all(r.state != "running" for r in runs.values())

    async def test_concurrent_runs_do_not_cross(self):
        reg = ActiveRunRegistry()
        # Two streams with different run ids / sessions, each finishing.
        port = _FakePort([_frame("delta", "run-1", "session:a:user:u"), _frame("final", "run-1", "session:a:user:u")])
        adapter = OpenClawChatAdapter(port, active_run_registry=reg)  # type: ignore[arg-type]
        await _drain(adapter, _req("session:a:user:u"))

        port2 = _FakePort([_frame("delta", "run-2", "session:b:user:u"), _frame("final", "run-2", "session:b:user:u")])
        adapter2 = OpenClawChatAdapter(port2, active_run_registry=reg)  # type: ignore[arg-type]
        await _drain(adapter2, _req("session:b:user:u"))

        runs = {r.run_id: r for r in reg.all_runs()}
        assert set(runs) == {"run-1", "run-2"}
        assert runs["run-1"].session_key == "session:a:user:u"
        assert runs["run-2"].session_key == "session:b:user:u"
        assert reg.snapshot() == []