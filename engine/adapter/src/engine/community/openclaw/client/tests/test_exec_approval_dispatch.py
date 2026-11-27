import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.community.openclaw.client import chat_dispatch
from engine.community.openclaw.client.exec_approval_dispatch import ExecApprovalDispatch
from engine.community.openclaw.client.gateway_client import OpenClawGatewayClient
from engine.community.openclaw.config import OpenClawConfig
from engine.community.openclaw.protocol import EventFrame, ResponseFrame

SESSION = "agent:main:session:approval-test"
RUN = "foreground"
FOLLOWUP = "exec-approval-followup:approval-1:nonce:native-nonce"


def approval(iid="approval-1", session=SESSION):
    return EventFrame(event="exec.approval.requested", payload={
        "id": iid, "request": {"sessionKey": session, "command": "printf test"},
        "expiresAtMs": time.time() * 1000 + 60_000,
    })


def agent(run=RUN, phase="end", session=SESSION):
    return EventFrame(event="agent", payload={
        "runId": run, "sessionKey": session, "stream": "lifecycle", "data": {"phase": phase},
    })


def final(run=RUN, text="waiting", session=SESSION):
    return EventFrame(event="chat", payload={
        "runId": run, "sessionKey": session, "state": "final",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    })


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(chat_dispatch, "POLL_SECONDS", 0.001)
    c = OpenClawGatewayClient(config=OpenClawConfig(gateway_url="ws://127.0.0.1:0"))
    c._connected = True
    c._hello = SimpleNamespace(server=SimpleNamespace(version="2026.5.12"),
                              features=SimpleNamespace(methods=["agent.wait"]))

    async def request(method, params, **kwargs):
        if method == "chat.send":
            return ResponseFrame.ok_response("ack", {"runId": RUN, "status": "started"})
        assert method == "agent.wait"
        return ResponseFrame.ok_response("wait", {"runId": params["runId"], "status": "ok", "endedAt": 123})

    c.send_request = AsyncMock(side_effect=request)
    return c


async def start_stream(client, enabled=True, timeout=1000):
    frames = []
    async def consume():
        async for frame in client.chat_stream(SESSION, "test", timeout_ms=timeout,
                                              wait_for_exec_approval=enabled):
            frames.append(frame)
    task = asyncio.create_task(consume())
    for _ in range(100):
        if client.send_request.called:
            break
        await asyncio.sleep(0.001)
    await client._dispatch_event(agent(phase="start"))
    return task, frames


async def finish_original(client):
    await client._dispatch_event(agent())
    await client._dispatch_event(final())
    await asyncio.sleep(0.02)


@pytest.mark.parametrize("decision", ["allow-once", "deny"])
async def test_waits_after_original_end_and_resolution_until_correlated_final(client, decision):
    task, frames = await start_stream(client)
    await client._dispatch_event(approval())
    await finish_original(client)
    assert not task.done()
    assert not any(f.get("state") == "final" for f in frames)
    await client._dispatch_event(EventFrame(event="exec.approval.resolved", payload={
        "id": "approval-1", "decision": decision,
    }))
    await asyncio.sleep(0.01)
    assert not task.done()
    # Same-session user turns and wrong-session followups are not ours.
    await client._dispatch_event(final("unrelated-user-run", "FOREIGN"))
    await client._dispatch_event(final(FOLLOWUP, "FOREIGN", session="session:other"))
    await client._dispatch_event(agent(FOLLOWUP, "start"))
    await client._dispatch_event(final(RUN, "stale-original-answer"))
    await client._dispatch_event(final(FOLLOWUP, f"actual-{decision}"))
    await asyncio.wait_for(task, 1)
    finals = [f for f in frames if f.get("state") == "final"]
    assert len(finals) == 1
    assert finals[0]["message"]["content"][0]["text"] == f"actual-{decision}"
    assert finals[0]["runId"] == FOLLOWUP
    assert not any("FOREIGN" in str(f) for f in frames)
    assert all(not handlers for handlers in client._event_listeners.values())
    assert sum(call.args[0] == "chat.send" for call in client.send_request.await_args_list) == 1


async def test_legacy_ws_still_finishes_at_original_completion(client):
    task, frames = await start_stream(client, enabled=False)
    await client._dispatch_event(approval())
    await finish_original(client)
    await asyncio.wait_for(task, 1)
    assert frames[-1]["state"] == "final"
    assert frames[-1]["runId"] == RUN


async def test_normal_a2a_without_approval_unchanged(client):
    task, frames = await start_stream(client)
    await finish_original(client)
    await asyncio.wait_for(task, 1)
    assert frames[-1]["state"] == "final"


async def test_unknown_continuation_times_out_without_success_or_resend(client):
    task, frames = await start_stream(client, timeout=70)
    await client._dispatch_event(approval())
    await finish_original(client)
    await asyncio.wait_for(task, 1)
    assert frames[-1]["errorCode"] == "EXEC_APPROVAL_CONTINUATION_UNCONFIRMED"
    assert not any(f.get("state") == "final" for f in frames)
    assert all(not handlers for handlers in client._event_listeners.values())


async def test_unrelated_approval_does_not_hold_foreground(client):
    task, frames = await start_stream(client)
    await client._dispatch_event(approval(session="session:other"))
    await finish_original(client)
    await asyncio.wait_for(task, 1)
    assert frames[-1]["state"] == "final"
    assert not any(f.get("state") == "approval_requested" for f in frames)


def test_followup_identity_requires_exact_approval_and_session(client):
    d = ExecApprovalDispatch(client, SESSION, RUN, "test")
    assert d.approval(approval(), "approval_requested")
    for run, session in [("unrelated", SESSION), (FOLLOWUP, "session:other"),
                          ("exec-approval-followup:approval-12", SESSION),
                          ("exec-approval-followup:approval-1:nonce:", SESSION)]:
        assert not d.correlate(agent(run, "start", session))
    assert d.correlate(agent(FOLLOWUP, "start"))
    assert not d.correlate(agent("exec-approval-followup:approval-1:nonce:another", "start"))


async def test_multiple_approvals_wait_for_all_followups(client):
    task, frames = await start_stream(client)
    await client._dispatch_event(approval())
    await client._dispatch_event(approval("approval-2"))
    await finish_original(client)
    await client._dispatch_event(agent(FOLLOWUP, "start"))
    await client._dispatch_event(final(FOLLOWUP, "first"))
    await asyncio.sleep(0.02)
    assert not task.done()
    second = "exec-approval-followup:approval-2"
    await client._dispatch_event(agent(second, "start"))
    await client._dispatch_event(final(second, "second"))
    await asyncio.wait_for(task, 1)
    assert frames[-1]["runId"] == second


@pytest.mark.parametrize("expiry", [0, True, float("nan"), float("inf")])
def test_invalid_expiry_is_not_registered(client, expiry):
    d = ExecApprovalDispatch(client, SESSION, RUN, "test")
    event = approval()
    event.payload["expiresAtMs"] = expiry
    assert not d.approval(event, "approval_requested")
    assert not d.pending


async def test_old_gateway_keeps_normal_a2a_chat_and_rejects_only_approval(client):
    client._hello.server.version = "2026.3.13"
    task, frames = await start_stream(client)
    await finish_original(client)
    await asyncio.wait_for(task, 1)
    assert frames[-1]["state"] == "final"
    client.send_request.reset_mock()
    task, frames = await start_stream(client)
    await client._dispatch_event(approval())
    await asyncio.wait_for(task, 1)
    assert frames[-1]["errorCode"] == "EXEC_APPROVAL_UNSUPPORTED"


async def test_followup_failure_is_not_reported_as_success(client):
    task, frames = await start_stream(client)
    await client._dispatch_event(approval())
    await finish_original(client)
    await client._dispatch_event(agent(FOLLOWUP, "start"))
    original_request = client.send_request.side_effect
    async def request(method, params, **kwargs):
        if method == "agent.wait" and params["runId"] == FOLLOWUP:
            return ResponseFrame.ok_response("wait", {"runId": FOLLOWUP, "status": "error", "endedAt": 124})
        return await original_request(method, params, **kwargs)
    client.send_request.side_effect = request
    await client._dispatch_event(agent(FOLLOWUP, "error"))
    await asyncio.wait_for(task, 1)
    assert frames[-1]["state"] == "error"
    assert not any(f.get("state") == "final" for f in frames)
    client.send_request.side_effect = original_request
    client.send_request.reset_mock()
    next_task, next_frames = await start_stream(client)
    await finish_original(client)
    await asyncio.wait_for(next_task, 1)
    assert next_frames[-1]["state"] == "final"


async def test_session_slot_is_held_until_approval_followup_finishes(client):
    task, frames = await start_stream(client)
    await client._dispatch_event(approval())
    await finish_original(client)
    queued = asyncio.create_task(anext(client.chat_stream(SESSION, "next", timeout_ms=100)))
    await asyncio.sleep(0.02)
    assert sum(call.args[0] == "chat.send" for call in client.send_request.await_args_list) == 1
    queued.cancel()
    await asyncio.gather(queued, return_exceptions=True)
    await client._dispatch_event(agent(FOLLOWUP, "start"))
    await client._dispatch_event(final(FOLLOWUP, "done"))
    await asyncio.wait_for(task, 1)
    assert frames[-1]["state"] == "final"
