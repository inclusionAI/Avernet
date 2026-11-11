"""Session tool mirrors must prevent premature announce completion."""
import asyncio

import pytest

from engine.community.openclaw.client.tests.test_child_run_completion import (
    CHILDREN, PARENT_RUN, PARENT_SESSION, StreamProbe, _stream,
)
from engine.community.openclaw.protocol import ResponseFrame

pytestmark = pytest.mark.asyncio


def spawn_mirror(stream, child, *, run=PARENT_RUN, session=PARENT_SESSION, **details):
    child_session, child_run = child
    stream.emit("session.tool", run=run, session=session, stream="tool", data={
        "name": "sessions_spawn", "phase": "result", "isError": False,
        "result": {"details": {
            "status": "accepted", "childSessionKey": child_session,
            "runId": child_run, **details,
        }},
    })


async def test_child_created_inside_announce_keeps_stream_open(monkeypatch):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.parent_final("waiting for A")
        stream.child_terminal(CHILDREN[0])
        announce_a = f"announce:v1:{CHILDREN[0][0]}:{CHILDREN[0][1]}"
        spawn_mirror(stream, CHILDREN[1], run=announce_a)
        stream.announce(CHILDREN[0], text="B started, still waiting")
        await stream.assert_open()
        stream.child_terminal(CHILDREN[1])
        stream.announce(CHILDREN[1], text="complete A and B")
        events = await stream.result()

    assert [event["text"] for event in events if event.get("state") == "final"] == [
        "complete A and B",
    ]
    assert not any(event.get("_source_event") == "session.tool" for event in events)


async def test_session_tool_and_agent_duplicate_register_child_once(monkeypatch):
    async with _stream(monkeypatch) as stream:
        spawn_mirror(stream, CHILDREN[0])
        stream.spawn(CHILDREN[0])
        spawn_mirror(stream, CHILDREN[0])
        stream.parent_final()
        stream.child_terminal(CHILDREN[0])
        stream.announce(CHILDREN[0], text="one completion")
        events = await stream.result()
    assert [e["text"] for e in events if e.get("state") == "final"] == ["one completion"]
    assert len(events) == 2  # Only the original tool frame and the final reach callers.


@pytest.mark.parametrize("run,session", [
    (PARENT_RUN, "agent:main:foreign"),
    ("unknown-run", PARENT_SESSION),
    ("announce:v1:agent:main:subagent:unknown:unknown-run", PARENT_SESSION),
])
async def test_foreign_or_unknown_session_tools_cannot_add_children(monkeypatch, run, session):
    async with _stream(monkeypatch) as stream:
        spawn_mirror(stream, CHILDREN[0], run=run, session=session)
        stream.parent_final("no authorized children")
        events = await stream.result()
    assert [event["text"] for event in events] == ["no authorized children"]


@pytest.mark.parametrize("details", [
    {"status": "forbidden"}, {"childSessionKey": ""}, {"runId": ""},
    {"runId": PARENT_RUN}, {"childSessionKey": PARENT_SESSION},
])
async def test_rejected_or_incomplete_session_tool_does_not_block_final(monkeypatch, details):
    async with _stream(monkeypatch) as stream:
        spawn_mirror(stream, CHILDREN[0], **details)
        stream.parent_final("nothing pending")
        events = await stream.result()
    assert events[-1]["text"] == "nothing pending"


async def test_session_tool_state_cannot_settle_announce(monkeypatch):
    async with _stream(monkeypatch) as stream:
        stream.spawn(CHILDREN[0])
        stream.parent_final()
        stream.child_terminal(CHILDREN[0])
        stream.emit(
            "session.tool", state="final", stream="tool",
            run=f"announce:v1:{CHILDREN[0][0]}:{CHILDREN[0][1]}",
            data={"name": "exec", "phase": "result"},
        )
        await stream.assert_open()
        stream.announce(CHILDREN[0], text="real final")
        events = await stream.result()
    assert events[-1]["text"] == "real final"


async def test_session_tool_before_ack_reconciles_early_child_terminal(monkeypatch):
    monkeypatch.setenv("OPENCLAW_EARLY_FINAL_GRACE_SECONDS", "0")
    stream = StreamProbe()

    async def send_request(method, params):
        stream.child_terminal(CHILDREN[0])
        spawn_mirror(stream, CHILDREN[0])
        stream.parent_final("completed before ack")
        return ResponseFrame.ok_response("request", {"runId": PARENT_RUN, "status": "started"})

    stream.client.send_request = send_request
    order = []

    async def on_ack(response):
        order.append("ack")

    events = []
    async for event in stream.client.chat_stream(
        "child-completion-parent", "test", idempotency_key=PARENT_RUN,
        timeout_ms=250, on_ack=on_ack,
    ):
        events.append(event)
        order.append(event.get("state"))
    assert order == ["ack", "final"]
    assert events[0]["text"] == "completed before ack"
    assert not any(stream.client._event_listeners.values())


async def test_mirror_spawn_invalidates_empty_final_grace(monkeypatch):
    from engine.community.openclaw.client import gateway_client

    monkeypatch.setattr(gateway_client, "_get_early_final_grace_seconds", lambda: 0.02)
    async with _stream(monkeypatch) as stream:
        stream.emit(state="final")
        spawn_mirror(stream, CHILDREN[0])
        finished, _ = await asyncio.wait({stream.task}, timeout=0.05)
        assert not finished, "The obsolete empty final escaped while the child was running"
        stream.child_terminal(CHILDREN[0])
        stream.parent_final("fresh complete result")
        events = await stream.result()
    assert [e.get("text") for e in events if e.get("state") == "final"] == ["fresh complete result"]
