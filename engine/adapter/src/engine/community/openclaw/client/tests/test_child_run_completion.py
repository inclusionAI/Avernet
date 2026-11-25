"""Child completion contracts exercised through the public chat stream."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from engine.community.openclaw.client.tests.test_gateway_client import (
    _fire_event,
    _make_client,
)
from engine.community.openclaw.protocol import ResponseFrame


PARENT_SESSION = "agent:main:child-completion-parent"
PARENT_RUN = "parent-run"
CHILDREN = (
    ("agent:main:subagent:alpha", "child-run-alpha"),
    ("agent:main:subagent:beta", "child-run-beta"),
    ("agent:main:subagent:gamma", "child-run-gamma"),
)
pytestmark = pytest.mark.asyncio


class StreamProbe:
    """Inject gateway frames while collecting only real chat_stream output."""

    def __init__(self):
        self.client = _make_client()
        self.events = []
        self.order = []
        self.acknowledged = asyncio.Event()

    async def collect(self):
        async def send_request(method, params):
            assert method == "chat.send"
            assert params["idempotencyKey"] == PARENT_RUN
            return ResponseFrame.ok_response("request", {
                "runId": PARENT_RUN, "status": "started",
            })

        async def on_ack(response):
            assert response.payload["runId"] == PARENT_RUN
            self.order.append("ack")
            self.acknowledged.set()

        self.client.send_request = send_request
        async for event in self.client.chat_stream(
            "child-completion-parent", "coordinate children",
            idempotency_key=PARENT_RUN, timeout_ms=250, on_ack=on_ack,
        ):
            self.events.append(event)
            self.order.append(event.get("state"))
        return self.events

    def emit(self, event="chat", *, run=PARENT_RUN, session=PARENT_SESSION, **payload):
        _fire_event(self.client, event, {
            "runId": run, "sessionKey": session, **payload,
        })

    def spawn(self, child, *, run=PARENT_RUN, session=PARENT_SESSION):
        child_session, child_run = child
        self.emit(run=run, session=session, state="delta", stream="tool", data={
            "name": "sessions_spawn", "phase": "result", "isError": False,
            "result": {"details": {
                "status": "accepted", "childSessionKey": child_session,
                "runId": child_run,
            }},
        })

    def child_terminal(self, child, state="final"):
        session, run = child
        self.emit(run=run, session=session, state=state, text="private child result")

    def parent_final(self, text="fresh parent result", *, run=PARENT_RUN):
        self.emit(run=run, state="final", text=text)

    def continuation(self, *, run="resumed-parent", session=PARENT_SESSION):
        self.emit("agent", run=run, session=session, stream="assistant", data={
            "delta": "parent processing results",
        })

    def announce(self, child, state="final", **payload):
        session, run = child
        self.emit(run=f"announce:v1:{session}:{run}", state=state, **payload)

    async def assert_open(self):
        finished, _ = await asyncio.wait({self.task}, timeout=0.01)
        assert not finished, f"Stream ended prematurely: {self.events!r}"

    async def result(self):
        events = await asyncio.wait_for(self.task, timeout=1)
        assert self.order[0] == "ack"
        assert not any(self.client._event_listeners.values())
        return events


@asynccontextmanager
async def _stream(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    probe = StreamProbe()
    probe.task = asyncio.create_task(probe.collect())
    try:
        await asyncio.wait_for(probe.acknowledged.wait(), timeout=1)
        yield probe
    finally:
        if not probe.task.done():
            probe.task.cancel()
        await asyncio.gather(probe.task, return_exceptions=True)


@pytest.mark.parametrize("session_key", ["agent:main:subagent:parent", "subagent:parent"])
async def test_parent_subagent_session_frames_are_not_observed_as_child(session_key):
    client = _make_client()

    async def send_request(method, params):
        assert method == "chat.send"
        assert params["sessionKey"] == session_key
        _fire_event(client, "agent", {
            "sessionKey": "agent:main:subagent:parent", "runId": PARENT_RUN,
            "stream": "assistant", "data": {"delta": "parent assistant output"},
        })
        _fire_event(client, "chat", {
            "sessionKey": "agent:main:subagent:parent", "runId": PARENT_RUN,
            "state": "final", "text": "parent final output",
        })
        return ResponseFrame.ok_response("request", {"runId": PARENT_RUN, "status": "started"})

    client.send_request = send_request

    async def collect():
        return [event async for event in client.chat_stream(
            session_key, "hello", idempotency_key=PARENT_RUN, timeout_ms=250,
        )]

    events = await asyncio.wait_for(collect(), timeout=1)

    assert [(event.get("runId"), event.get("state")) for event in events] == [
        (PARENT_RUN, None), (PARENT_RUN, "final"),
    ]
    assert events[0]["data"] == {"delta": "parent assistant output"}
    assert events[-1]["text"] == "parent final output"
    assert not any(client._event_listeners.values())


async def test_three_children_complete_before_original_parent_final(monkeypatch):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN:
            stream.spawn(child)
        for child in CHILDREN:
            stream.child_terminal(child)
        stream.parent_final()
        events = await stream.result()

    assert [(event.get("runId"), event["state"]) for event in events] == [
        (PARENT_RUN, "delta"), (PARENT_RUN, "delta"), (PARENT_RUN, "delta"),
        (PARENT_RUN, "final"),
    ]
    assert events[-1]["text"] == "fresh parent result"


@pytest.mark.parametrize("resume_run", [PARENT_RUN, "resumed-parent"])
async def test_three_children_after_deferred_final_need_only_one_resume(
    monkeypatch, resume_run,
):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN:
            stream.spawn(child)
        stream.parent_final("obsolete parent final")
        for child in CHILDREN:
            stream.child_terminal(child)
        await stream.assert_open()
        stream.continuation(run=resume_run)
        stream.parent_final(run=resume_run)
        events = await stream.result()

    assert [(event.get("runId"), event.get("state")) for event in events] == [
        (PARENT_RUN, "delta"), (PARENT_RUN, "delta"), (PARENT_RUN, "delta"),
        (resume_run, None), (resume_run, "final"),
    ]
    assert events[-1]["text"] == "fresh parent result"


async def test_same_run_progress_before_final_cannot_complete_a_child(monkeypatch):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.continuation(run=PARENT_RUN)
        stream.parent_final("child still pending")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[0])
        await stream.assert_open()
        stream.continuation(run=PARENT_RUN)
        stream.parent_final()
        events = await stream.result()

    assert [event.get("state") for event in events] == ["delta", None, None, "final"]
    assert events[-1]["text"] == "fresh parent result"


async def test_last_child_does_not_flush_old_final_or_authorize_final_without_progress(
    monkeypatch,
):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.parent_final("obsolete parent final")
        stream.child_terminal(CHILDREN[0])
        await stream.assert_open()
        stream.parent_final("repeated final without continuation")
        await stream.assert_open()
        stream.continuation(run=PARENT_RUN)
        stream.parent_final()
        events = await stream.result()

    assert [event.get("state") for event in events] == ["delta", None, "final"]
    assert events[-1]["text"] == "fresh parent result"


async def test_duplicate_spawn_and_terminal_cannot_overcount_or_settle_another_child(
    monkeypatch,
):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.spawn(CHILDREN[0])
        stream.spawn(CHILDREN[1])
        stream.parent_final("obsolete parent final")
        stream.child_terminal(CHILDREN[0])
        stream.child_terminal(CHILDREN[0])
        stream.continuation()
        stream.parent_final("second child still pending", run="resumed-parent")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[1])
        stream.child_terminal(CHILDREN[1])
        stream.spawn(CHILDREN[0], run="resumed-parent")
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert events[-1]["state"] == "final"
    assert events[-1]["text"] == "fresh parent result"
    assert not any(event.get("runId") in {"child-run-alpha", "child-run-beta"} for event in events)
    assert [event.get("text") for event in events if event.get("state") == "final"] == [
        "fresh parent result",
    ]


@pytest.mark.parametrize("session,run", [
    ("agent:main:subagent:wrong", "child-run-beta"),
    ("agent:main:subagent:beta", "wrong-child-run"),
    ("agent:main:subagent:beta", "child-run-alpha"),
    ("agent:other:subagent:beta", "child-run-beta"),
    (None, "child-run-beta"),
])
async def test_child_terminal_requires_both_registered_session_and_run(
    monkeypatch, session, run,
):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN[:2]:
            stream.spawn(child)
        stream.parent_final("obsolete parent final")
        stream.child_terminal(CHILDREN[0])
        stream.emit(session=session, run=run, state="final", text="mismatched child")
        stream.continuation()
        stream.parent_final("second child still pending", run="resumed-parent")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[1])
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert events[-1]["state"] == "final"
    assert events[-1]["text"] == "fresh parent result"
    assert not any(event.get("text") == "mismatched child" for event in events)


async def test_conflicting_spawn_identity_does_not_replace_registered_session(monkeypatch):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        conflicting_child = ("agent:main:subagent:impostor", "child-run-alpha")
        stream.spawn(conflicting_child)
        stream.parent_final("obsolete parent final")
        stream.child_terminal(conflicting_child)
        stream.continuation()
        stream.parent_final("real child still pending", run="resumed-parent")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[0])
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert events[-1]["state"] == "final"
    assert events[-1]["text"] == "fresh parent result"


async def test_foreign_nested_spawn_does_not_register_a_direct_child(monkeypatch):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.spawn(CHILDREN[1], session=CHILDREN[0][0], run=CHILDREN[0][1])
        stream.child_terminal(CHILDREN[1])
        stream.child_terminal(CHILDREN[0])
        stream.parent_final()
        events = await stream.result()

    assert [(event.get("runId"), event["state"]) for event in events] == [
        (PARENT_RUN, "delta"), (PARENT_RUN, "final"),
    ]


@pytest.mark.parametrize("first_event", [
    "assistant", "approval.requested", "approval.resolved", "final",
])
async def test_unrelated_same_session_run_cannot_resume_without_child_completion(
    monkeypatch, first_event,
):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.parent_final("obsolete parent final")
        if first_event == "assistant":
            stream.continuation(run="unrelated-run")
        elif first_event == "final":
            stream.parent_final("unrelated final", run="unrelated-run")
        else:
            stream.emit(first_event, run="unrelated-run", approvalId="unrelated-approval")
        stream.parent_final("unrelated final", run="unrelated-run")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[0])
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert not any(event.get("runId") == "unrelated-run" for event in events)
    assert events[-1]["state"] == "final"
    assert events[-1]["text"] == "fresh parent result"


async def test_completed_child_does_not_authorize_new_run_before_parent_defer(monkeypatch):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.child_terminal(CHILDREN[0])
        stream.continuation(run="unrelated-run")
        stream.parent_final("unrelated final", run="unrelated-run")
        await stream.assert_open()
        stream.parent_final()
        events = await stream.result()

    assert not any(event.get("runId") == "unrelated-run" for event in events)
    assert events[-1]["state"] == "final"
    assert events[-1]["runId"] == PARENT_RUN
    assert events[-1]["text"] == "fresh parent result"


@pytest.mark.parametrize("children_already_ended", [False, True])
async def test_announce_fan_in_requires_all_unique_registered_announces(
    monkeypatch, children_already_ended,
):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN:
            stream.spawn(child)
        stream.parent_final("obsolete parent final")
        if children_already_ended:
            for child in CHILDREN:
                stream.child_terminal(child)
        stream.announce(CHILDREN[0], state="delta", text="partial delivery")
        stream.announce(CHILDREN[0], text="first delivery")
        await stream.assert_open()
        stream.announce(CHILDREN[0], text="duplicate first delivery")
        await stream.assert_open()
        stream.announce(CHILDREN[1], text="second delivery")
        await stream.assert_open()
        stream.announce(CHILDREN[2], text="all child results delivered")
        events = await stream.result()

    assert [(event["runId"], event.get("text")) for event in events if event["state"] == "final"] == [
        ("announce:v1:agent:main:subagent:gamma:child-run-gamma", "all child results delivered"),
    ]


async def test_duplicate_old_parent_final_does_not_cover_completed_child(monkeypatch):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN[:2]:
            stream.spawn(child)
        stream.parent_final("old parent final")
        stream.child_terminal(CHILDREN[0])
        stream.parent_final("old parent final")
        stream.announce(CHILDREN[1], text="B delivered")
        await stream.assert_open()
        stream.announce(CHILDREN[0], text="A delivered after B")
        events = await stream.result()

    assert [(event.get("runId"), event.get("state")) for event in events] == [
        (PARENT_RUN, "delta"), (PARENT_RUN, "delta"),
        ("announce:v1:agent:main:subagent:alpha:child-run-alpha", "final"),
    ]
    assert events[-1]["text"] == "A delivered after B"


async def test_stale_original_final_cannot_end_authorized_new_parent_run(monkeypatch, caplog):
    caplog.set_level("INFO", logger="openclaw-client")
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.parent_final("old root final")
        stream.child_terminal(CHILDREN[0])
        stream.continuation(run="resumed-parent")
        stream.parent_final("old root final")
        await stream.assert_open()
        assert any(
            "[stale_parent_final]" in message and f"runId={PARENT_RUN}" in message
            for message in caplog.messages
        )
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert [(event.get("runId"), event.get("state")) for event in events] == [
        (PARENT_RUN, "delta"), ("resumed-parent", None), ("resumed-parent", "final"),
    ]
    assert events[-1]["text"] == "fresh parent result"


@pytest.mark.parametrize("send_old_final", [True, False], ids=["reject-old-final", "accept-current-final"])
async def test_superseded_spawn_result_cannot_rewind_resumed_parent(
    monkeypatch, send_old_final,
):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN[:2]:
            stream.spawn(child)
        stream.parent_final("old root final")
        stream.child_terminal(CHILDREN[0])
        stream.continuation(run="resumed-parent")
        stream.parent_final("B still pending", run="resumed-parent")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[1])
        stream.spawn(CHILDREN[0], run=PARENT_RUN)
        if send_old_final:
            stream.parent_final("superseded root final", run=PARENT_RUN)
            await stream.assert_open()
        stream.continuation(run="resumed-parent")
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert [event.get("text") for event in events if event.get("state") == "final"] == [
        "fresh parent result",
    ]
    assert events[-1]["state"] == "final"
    assert events[-1]["runId"] == "resumed-parent"


async def test_pre_ack_empty_final_cannot_authorize_unrelated_run_with_pending_child(
    monkeypatch,
):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0.05")
    stream = StreamProbe()

    async def send_request(method, params):
        assert method == "chat.send"
        assert params["idempotencyKey"] == PARENT_RUN
        stream.emit(state="final")
        stream.spawn(CHILDREN[0])
        stream.parent_final("old parent final")
        stream.continuation(run="unrelated-run")
        stream.emit(run="unrelated-run", state="error", errorMessage="unrelated failed")
        return ResponseFrame.ok_response("request", {"runId": PARENT_RUN, "status": "started"})

    async def on_ack(response):
        assert response.payload["runId"] == PARENT_RUN
        stream.order.append("ack")
        stream.acknowledged.set()

    async def collect():
        async for event in stream.client.chat_stream(
            "child-completion-parent", "coordinate children",
            idempotency_key=PARENT_RUN, timeout_ms=250, on_ack=on_ack,
        ):
            stream.events.append(event)
            stream.order.append(event.get("state"))
        return stream.events

    stream.client.send_request = send_request
    stream.task = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(stream.acknowledged.wait(), timeout=1)
        await stream.assert_open()
        assert not any(event.get("runId") == "unrelated-run" for event in stream.events)
        stream.child_terminal(CHILDREN[0])
        await stream.assert_open()
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()
    finally:
        if not stream.task.done():
            stream.task.cancel()
        await asyncio.gather(stream.task, return_exceptions=True)

    assert [(event.get("runId"), event.get("state")) for event in events] == [
        (PARENT_RUN, "delta"), ("resumed-parent", None), ("resumed-parent", "final"),
    ]
    assert events[-1]["text"] == "fresh parent result"


@pytest.mark.parametrize("duplicate_covered_announce", [False, True])
async def test_parent_covered_child_does_not_block_remaining_child_announce(
    monkeypatch, duplicate_covered_announce,
):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN[:2]:
            stream.spawn(child)
        stream.parent_final("obsolete parent final")
        stream.child_terminal(CHILDREN[0])
        await stream.assert_open()
        stream.continuation()
        stream.parent_final("A covered but B still pending", run="resumed-parent")
        await stream.assert_open()
        if duplicate_covered_announce:
            stream.announce(CHILDREN[0], text="late covered A delivery")
            await stream.assert_open()
            stream.announce(CHILDREN[0], text="duplicate covered A delivery")
            await stream.assert_open()
        stream.announce(CHILDREN[1], text="B completes the mixed path")
        events = await stream.result()

    assert [(event["runId"], event.get("text")) for event in events if event.get("state") == "final"] == [
        ("announce:v1:agent:main:subagent:beta:child-run-beta", "B completes the mixed path"),
    ]
    assert not any(event.get("runId") == "child-run-alpha" for event in events)


@pytest.mark.parametrize("announce_run,session", [
    ("announce:anything", PARENT_SESSION),
    ("announce:v2:agent:main:subagent:alpha:child-run-alpha", PARENT_SESSION),
    ("announce:v1:agent:main:subagent:alpha:wrong-run", PARENT_SESSION),
    ("announce:v1:agent:main:subagent:foreign:child-run-alpha", PARENT_SESSION),
    ("announce:v1:agent:main:subagent:beta:child-run-alpha", PARENT_SESSION),
    ("announce:v1:agent:main:subagent:alpha:child-run-alpha:extra", PARENT_SESSION),
    ("announce:v1:agent:main:subagent:alpha:child-run-alpha", "agent:main:other-parent"),
])
async def test_unmatched_announce_does_not_authorize_completion(
    monkeypatch, announce_run, session,
):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.parent_final("obsolete parent final")
        stream.emit(run=announce_run, session=session, state="final", text="unmatched announce")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[0])
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert not any(event.get("text") == "unmatched announce" for event in events)
    assert events[-1]["state"] == "final"
    assert events[-1]["text"] == "fresh parent result"


async def test_child_terminal_before_accepted_spawn_is_reconciled(monkeypatch):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN:
            stream.child_terminal(child)
        for child in CHILDREN:
            stream.spawn(child)
        stream.parent_final()
        events = await stream.result()

    assert [event["state"] for event in events] == ["delta", "delta", "delta", "final"]
    assert events[-1]["text"] == "fresh parent result"


async def test_early_terminal_cache_evicts_oldest_and_retains_newest(monkeypatch):
    early_children = tuple(
        (f"agent:main:subagent:early-{index}", f"early-run-{index}")
        for index in range(129)
    )
    async with _stream(monkeypatch) as stream:
        for child in early_children:
            stream.child_terminal(child)
        stream.spawn(early_children[0])
        stream.spawn(early_children[-1])
        stream.parent_final("evicted oldest child still pending")
        await stream.assert_open()
        stream.continuation()
        stream.parent_final("retained newest cannot settle oldest", run="resumed-parent")
        await stream.assert_open()
        stream.child_terminal(early_children[0])
        await stream.assert_open()
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert [(event.get("runId"), event.get("state")) for event in events] == [
        (PARENT_RUN, "delta"), (PARENT_RUN, "delta"),
        ("resumed-parent", None), ("resumed-parent", None),
        ("resumed-parent", "final"),
    ]
    assert events[-1]["text"] == "fresh parent result"


@pytest.mark.parametrize("before_spawn", [False, True], ids=["accepted", "before-accepted"])
async def test_child_compaction_intermediate_final_does_not_complete_child(
    monkeypatch, caplog, before_spawn,
):
    caplog.set_level("INFO", logger="openclaw-client")
    child_session, child_run = CHILDREN[0]
    async with _stream(monkeypatch) as stream:
        if not before_spawn:
            stream.spawn(CHILDREN[0])
        stream.emit("agent", session=child_session, run=child_run,
                    stream="compaction", data={"phase": "end", "willRetry": True})
        stream.emit("agent", session=child_session, run=child_run,
                    stream="lifecycle", data={"phase": "end"})
        stream.child_terminal(CHILDREN[0])
        if before_spawn:
            stream.spawn(CHILDREN[0])
        stream.parent_final("obsolete parent final during child compaction")
        await stream.assert_open()

        suppressed = [
            message for message in caplog.messages
            if "[child_compaction_final_suppressed]" in message
        ]
        assert len(suppressed) == 1
        assert f"childRunId={child_run}" in suppressed[0]
        assert f"sessionKey={child_session}" in suppressed[0]
        assert not any("[child_terminal]" in message for message in caplog.messages)

        stream.continuation(run=child_run, session=child_session)
        stream.child_terminal(CHILDREN[0])
        await stream.assert_open()
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert [(event.get("runId"), event.get("state")) for event in events] == [
        (PARENT_RUN, "delta"), ("resumed-parent", None), ("resumed-parent", "final"),
    ]
    assert events[-1]["text"] == "fresh parent result"
    assert sum("[child_terminal]" in message for message in caplog.messages) == 1
    assert "private child result" not in caplog.text


@pytest.mark.parametrize("continuation_event", ["agent", "chat"])
async def test_child_compaction_continuation_without_intermediate_terminal(
    monkeypatch, caplog, continuation_event,
):
    caplog.set_level("INFO", logger="openclaw-client")
    child_session, child_run = CHILDREN[0]
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.emit("agent", session=child_session, run=child_run,
                    stream="compaction", data={"phase": "end", "willRetry": True})
        stream.parent_final("obsolete parent final during child compaction")
        await stream.assert_open()
        if continuation_event == "agent":
            stream.continuation(run=child_run, session=child_session)
        else:
            stream.emit(session=child_session, run=child_run, state="delta",
                        text="private child continuation")
        stream.emit("agent", session=child_session, run=child_run,
                    stream="lifecycle", data={"phase": "end"})
        stream.child_terminal(CHILDREN[0])
        await stream.assert_open()
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert [(event.get("runId"), event.get("state")) for event in events] == [
        (PARENT_RUN, "delta"), ("resumed-parent", None), ("resumed-parent", "final"),
    ]
    assert events[-1]["text"] == "fresh parent result"
    assert not any("[child_compaction_final_suppressed]" in message for message in caplog.messages)
    assert sum("[child_terminal]" in message for message in caplog.messages) == 1
    assert "private child result" not in caplog.text
    assert "private child continuation" not in caplog.text


async def test_early_terminal_cache_does_not_settle_mismatched_registered_child(monkeypatch):
    async with _stream(monkeypatch) as stream:
        stream.child_terminal(("agent:main:subagent:foreign", "child-run-alpha"))
        stream.spawn(CHILDREN[0])
        stream.parent_final("real child still pending")
        stream.continuation(run="unrelated-run")
        stream.parent_final("unrelated final", run="unrelated-run")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[0])
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert not any(event.get("runId") == "unrelated-run" for event in events)
    assert events[-1]["state"] == "final"
    assert events[-1]["text"] == "fresh parent result"


@pytest.mark.parametrize("terminal_state", ["error", "aborted"])
async def test_child_error_or_abort_settles_only_that_child(monkeypatch, terminal_state):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN[:2]:
            stream.spawn(child)
        stream.parent_final("obsolete parent final")
        stream.child_terminal(CHILDREN[0], terminal_state)
        stream.child_terminal(CHILDREN[0], terminal_state)
        stream.continuation()
        stream.parent_final("second child still pending", run="resumed-parent")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[1])
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert not any(event.get("state") in {"error", "aborted"} for event in events)
    assert not any(event.get("runId") in {"child-run-alpha", "child-run-beta"} for event in events)
    assert events[-1]["state"] == "final"
    assert events[-1]["text"] == "fresh parent result"


async def test_child_lifecycle_end_is_not_chat_completion(monkeypatch):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.parent_final("obsolete parent final")
        stream.emit("agent", session=CHILDREN[0][0], run=CHILDREN[0][1],
                    stream="lifecycle", data={"phase": "end"})
        stream.continuation(run="unrelated-run")
        stream.parent_final("unrelated final", run="unrelated-run")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[0])
        stream.continuation()
        stream.parent_final(run="resumed-parent")
        events = await stream.result()

    assert not any(event.get("runId") == "unrelated-run" for event in events)
    assert events[-1]["state"] == "final"
    assert events[-1]["text"] == "fresh parent result"


@pytest.mark.parametrize("terminal_state", ["error", "aborted"])
async def test_parent_hard_stop_terminates_with_children_pending(monkeypatch, terminal_state):
    async with _stream(monkeypatch) as stream:
        for child in CHILDREN:
            stream.spawn(child)
        stream.emit(state=terminal_state, errorMessage="parent stopped")
        events = await stream.result()

    assert [event["state"] for event in events] == ["delta", "delta", "delta", terminal_state]
    assert events[-1]["runId"] == PARENT_RUN
