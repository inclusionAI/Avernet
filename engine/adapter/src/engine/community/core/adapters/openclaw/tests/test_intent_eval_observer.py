"""Phase B tests — IntentEvalObserver.

Feeds synthesized EventFrame sequences into the observer and asserts the
expected intent_eval emissions fired, in the right order, with the right
metadata.

The observer calls intent_eval functions via `asyncio.create_task(...)`.
Tests patch `asyncio.create_task` to capture the coroutines so they can be
inspected before being awaited (or silently dropped).

Note: the observer also calls `emit_dialog_event` during `__init__`. Tests
use `setup()` to reset the patched create_task capture before constructing
the observer so the dialog-event task is visible to the first assertion.
"""
from __future__ import annotations

import pytest

from engine.community.kernel.frames import EventFrame
from engine.community.core.adapters.openclaw.intent_eval_observer import IntentEvalObserver


@pytest.fixture
def captured_tasks(monkeypatch):
    """Capture `asyncio.create_task` calls without actually scheduling them.

    Swaps the observer module's `emit_*` and `asyncio` references so each
    call gets recorded into a simple list. Direct attribute swap — `patch(
    side_effect=...)` didn't cooperate when the patched function needed to
    return a real awaitable.
    """
    import engine.community.core.adapters.openclaw.intent_eval_observer as mod

    tasks: list[tuple[str, dict]] = []

    def _record(name):
        def _impl(**kwargs):
            tasks.append((name, kwargs))
            async def _noop():
                return None
            return _noop()
        return _impl

    class _FakeAsyncio:
        @staticmethod
        def create_task(coro):
            # Close to suppress "coroutine never awaited" warnings.
            coro.close()
            return object()

    monkeypatch.setattr(mod, "emit_dialog_event", _record("dialog"))
    monkeypatch.setattr(mod, "emit_agent_response", _record("agent_response"))
    monkeypatch.setattr(mod, "emit_agent_complete", _record("agent_complete"))
    monkeypatch.setattr(mod, "asyncio", _FakeAsyncio)
    yield tasks


def _ev(event: str = "agent", **payload_fields) -> EventFrame:
    return EventFrame(event=event, payload=payload_fields)


# ─────────────────────────────────────────────────────────────────────────────
# Construction fires emit_dialog_event
# ─────────────────────────────────────────────────────────────────────────────


class TestConstruction:
    def test_fires_dialog_event_on_init(self, captured_tasks):
        IntentEvalObserver(
            session_key="sk-1",
            user_message="hello",
            token="tok-a",
        )
        assert len(captured_tasks) == 1
        name, kwargs = captured_tasks[0]
        assert name == "dialog"
        assert kwargs["session_id"] == "sk-1"
        assert kwargs["message"] == "hello"
        assert kwargs["token"] == "tok-a"


# ─────────────────────────────────────────────────────────────────────────────
# Final-state emission (state=final wins over lifecycle.end)
# ─────────────────────────────────────────────────────────────────────────────


class TestFinalEmission:
    def test_state_final_emits_agent_response(self, captured_tasks):
        obs = IntentEvalObserver(session_key="sk", user_message="hi", token=None)
        captured_tasks.clear()
        obs.observe(_ev(state="final", text="the full reply"))
        # state=final triggers agent_response
        names = [t[0] for t in captured_tasks]
        assert "agent_response" in names
        ar = next(kwargs for name, kwargs in captured_tasks if name == "agent_response")
        assert ar["message"] == "the full reply"

    def test_lifecycle_end_emits_agent_response(self, captured_tasks):
        obs = IntentEvalObserver(session_key="sk", user_message="hi", token=None)
        captured_tasks.clear()
        # Accumulate text first via assistant stream
        obs.observe(_ev(stream="assistant", text="running reply"))
        obs.observe(
            EventFrame(
                event="agent",
                payload={"stream": "lifecycle", "data": {"phase": "end"}},
            )
        )
        names = [t[0] for t in captured_tasks]
        assert "agent_response" in names
        ar = next(kwargs for name, kwargs in captured_tasks if name == "agent_response")
        assert ar["message"] == "running reply"

    def test_deduped_when_both_fire(self, captured_tasks):
        """lifecycle.end followed by state=final should emit exactly once."""
        obs = IntentEvalObserver(session_key="sk", user_message="hi", token=None)
        captured_tasks.clear()
        obs.observe(_ev(stream="assistant", text="reply"))
        obs.observe(
            EventFrame(
                event="agent",
                payload={"stream": "lifecycle", "data": {"phase": "end"}},
            )
        )
        obs.observe(_ev(state="final", text="reply"))
        names = [t[0] for t in captured_tasks]
        assert names.count("agent_response") == 1


# ─────────────────────────────────────────────────────────────────────────────
# Thinking emission
# ─────────────────────────────────────────────────────────────────────────────


class TestThinking:
    def test_thinking_done_flushes_accumulated_thinking(self, captured_tasks):
        obs = IntentEvalObserver(session_key="sk", user_message="hi", token=None)
        captured_tasks.clear()
        obs.observe(_ev(state="thinking_delta", text="reasoning so far"))
        obs.observe(_ev(state="thinking_done"))
        ar = [kwargs for name, kwargs in captured_tasks if name == "agent_response"]
        assert len(ar) == 1
        assert ar[0]["message"] == "reasoning so far"
        assert ar[0]["metadata"] == {"type": "thinking"}

    def test_thinking_done_with_empty_buffer_emits_nothing(
        self, captured_tasks,
    ):
        obs = IntentEvalObserver(session_key="sk", user_message="hi", token=None)
        captured_tasks.clear()
        obs.observe(_ev(state="thinking_done"))
        assert [n for n, _ in captured_tasks] == []


# ─────────────────────────────────────────────────────────────────────────────
# Tool calls — both Moltis (state=tool_call_end) and OpenClaw (stream=tool)
# ─────────────────────────────────────────────────────────────────────────────


class TestToolCalls:
    def test_moltis_tool_call_end_emits_tool_response(self, captured_tasks):
        obs = IntentEvalObserver(session_key="sk", user_message="hi", token=None)
        captured_tasks.clear()
        obs.observe(
            _ev(
                state="tool_call_end",
                toolName="write",
                result="file written",
                success=True,
            )
        )
        ar = [kwargs for name, kwargs in captured_tasks if name == "agent_response"]
        assert len(ar) == 1
        assert ar[0]["metadata"]["type"] == "tool_call"
        assert ar[0]["metadata"]["toolName"] == "write"
        assert ar[0]["metadata"]["isError"] is False
        assert "file written" in ar[0]["message"]

    def test_openclaw_tool_flushes_text_before_tool(self, captured_tasks):
        """Buffered assistant text flushes as its own agent_response when a
        tool call starts — legacy behaviour preserved."""
        obs = IntentEvalObserver(session_key="sk", user_message="hi", token=None)
        captured_tasks.clear()
        # Buffer some text, then a tool result.
        obs.observe(_ev(stream="assistant", text="let me call a tool"))
        obs.observe(
            EventFrame(
                event="agent",
                payload={
                    "stream": "tool",
                    "data": {"phase": "result", "name": "read"},
                    "text": "content",
                },
            )
        )
        ar = [kwargs for name, kwargs in captured_tasks if name == "agent_response"]
        # Expect at least two: the flushed text, then the tool result.
        messages = [k["message"] for k in ar]
        assert any("let me call a tool" in m for m in messages)
        assert any("Tool: read" in m for m in messages)


# ─────────────────────────────────────────────────────────────────────────────
# finalize()
# ─────────────────────────────────────────────────────────────────────────────


class TestFinalize:
    def test_finalize_after_state_final_emits_agent_complete(
        self, captured_tasks,
    ):
        obs = IntentEvalObserver(
            session_key="sk", user_message="user q", token=None,
        )
        captured_tasks.clear()
        obs.observe(_ev(state="final", text="the answer"))
        captured_tasks.clear()  # discard the response emit
        obs.finalize()
        ac = [kwargs for name, kwargs in captured_tasks if name == "agent_complete"]
        assert len(ac) == 1
        assert ac[0]["user_message"] == "user q"
        assert ac[0]["agent_message"] == "the answer"

    def test_finalize_without_terminal_state_is_noop(self, captured_tasks):
        obs = IntentEvalObserver(session_key="sk", user_message="q", token=None)
        captured_tasks.clear()
        # Stream ended without ever hitting final/aborted/lifecycle_end.
        obs.finalize()
        names = [n for n, _ in captured_tasks]
        assert "agent_complete" not in names

    def test_finalize_after_aborted_emits_agent_complete(self, captured_tasks):
        obs = IntentEvalObserver(session_key="sk", user_message="q", token=None)
        captured_tasks.clear()
        obs.observe(_ev(stream="assistant", text="partial"))
        obs.observe(_ev(state="aborted"))
        captured_tasks.clear()
        obs.finalize()
        ac = [kwargs for name, kwargs in captured_tasks if name == "agent_complete"]
        assert len(ac) == 1
        assert ac[0]["agent_message"] == "partial"
