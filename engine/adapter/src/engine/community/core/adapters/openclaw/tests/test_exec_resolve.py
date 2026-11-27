import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest

from engine.community.core.adapters.openclaw.exec_resolve import ExecApprovalLedger
from engine.community.core.adapters.openclaw.chat import OpenClawChatAdapter
from engine.community.core.chat.models import ChatRequest
from engine.community.kernel.frames import EventFrame, ResponseFrame, ErrorShape
from engine.community.plugins.openclaw._chat import _ChatPortMixin
from engine.community.api.transport.a2a import router as R, engine_bridge as bridge
from engine.community.api.transport.a2a.card import make_agent_card, OPENCLAW_EXEC_RESOLVE_URI
from engine.community.api.transport.a2a.kernel import A2aKernel
from engine.community.api.transport.a2a.resolve import exec_resolve_supported


def requested(session="session:one", iid="approval-1", **overrides):
    return {"state": "approval_requested", "id": iid, "approvalId": iid,
            "request": {"sessionKey": session, "command": "echo test"},
            "expiresAtMs": time.time() * 1000 + 60_000, **overrides}


@pytest.mark.parametrize("decision", ["allow-once", "deny"])
async def test_resolve_uses_exact_id_session_and_upstream_identity(decision):
    ledger = ExecApprovalLedger()
    assert ledger.normalize("session:one", requested(), "caller")
    port = Mock(exec_approval_resolve=AsyncMock(return_value={"success": True}))
    assert (await ledger.resolve(port, session_key="session:one",
                                interaction_id="approval-1", decision=decision))["success"]
    port.exec_approval_resolve.assert_awaited_once_with(
        approval_id="approval-1", decision=decision, token="caller")
    assert not (await ledger.resolve(port, session_key="session:one",
                                    interaction_id="approval-1", decision=decision))["success"]
    assert port.exec_approval_resolve.await_count == 1


@pytest.mark.parametrize("payload", [
    requested("session:other"), requested(request={}), requested(expiresAtMs=0),
    requested(expiresAtMs=True), requested(expiresAtMs=float("nan")),
    requested(expiresAtMs=float("inf")), requested(id="", approvalId=""),
])
def test_untrusted_or_expired_event_is_not_registered(payload):
    assert ExecApprovalLedger().normalize("session:one", payload, None) is None


async def test_cross_session_and_invalid_decision_never_dispatch():
    ledger = ExecApprovalLedger()
    ledger.normalize("session:one", requested(), None)
    port = Mock(exec_approval_resolve=AsyncMock())
    for session, decision, message in [("session:other", "deny", None),
                                       ("session:one", "approved", None),
                                       ("session:one", "allow-always", None),
                                       ("session:one", "deny", "unsupported")]:
        assert not (await ledger.resolve(port, session_key=session, interaction_id="approval-1",
                                        decision=decision, message=message))["success"]
    port.exec_approval_resolve.assert_not_awaited()


async def test_lost_response_blocks_duplicate_dispatch():
    ledger = ExecApprovalLedger()
    ledger.normalize("session:one", requested(), None)
    port = Mock(exec_approval_resolve=AsyncMock(side_effect=TimeoutError))
    for _ in range(2):
        result = await ledger.resolve(port, session_key="session:one",
                                      interaction_id="approval-1", decision="deny")
        assert result["error"]["code"] == "INTERNAL_ERROR"
    assert port.exec_approval_resolve.await_count == 1


async def test_concurrent_submission_dispatches_once():
    ledger = ExecApprovalLedger()
    ledger.normalize("session:one", requested(), None)
    async def success(**kwargs):
        await asyncio.sleep(0)
        return {"success": True}
    port = Mock(exec_approval_resolve=AsyncMock(side_effect=success))
    results = await asyncio.gather(*[ledger.resolve(
        port, session_key="session:one", interaction_id="approval-1", decision="deny") for _ in range(2)])
    assert sum(result["success"] for result in results) == 1
    assert port.exec_approval_resolve.await_count == 1


async def test_expiry_at_submission_and_native_resolution():
    ledger = ExecApprovalLedger()
    ledger.normalize("session:one", requested(), None)
    ledger.pending["approval-1"].expires_at = 0
    port = Mock(exec_approval_resolve=AsyncMock())
    assert not (await ledger.resolve(port, session_key="session:one",
                                    interaction_id="approval-1", decision="deny"))["success"]
    ledger.normalize("session:one", requested(), None)
    assert ledger.normalize("session:other", requested(state="approval_resolved"), None) is None
    assert ledger.normalize("session:one", requested(state="approval_resolved"), None)
    assert not ledger.pending
    port.exec_approval_resolve.assert_not_awaited()


@pytest.mark.parametrize("ok", [True, False])
async def test_native_port_uses_current_gateway_schema(ok):
    response = ResponseFrame(id="req", ok=ok, payload={},
                             error=None if ok else ErrorShape("INVALID_REQUEST", "expired"))
    client = Mock(send_request=AsyncMock(return_value=response))
    port = _ChatPortMixin()
    port._pooled_client = AsyncMock(return_value=client)
    result = await port.exec_approval_resolve("approval-exact", "allow-once", token="caller")
    assert result["success"] is ok
    port._pooled_client.assert_awaited_once_with("caller")
    client.send_request.assert_awaited_once_with(
        "exec.approval.resolve", {"id": "approval-exact", "decision": "allow-once"}, timeout=30.0)


async def test_native_invalid_decision_never_connects():
    port = _ChatPortMixin()
    port._pooled_client = AsyncMock()
    assert not (await port.exec_approval_resolve("id", "approved"))["success"]
    port._pooled_client.assert_not_awaited()


async def test_a2a_stream_and_resolve_close_the_same_run(monkeypatch):
    accepted = asyncio.Event()
    async def frames(**kwargs):
        yield EventFrame(event="exec.approval.requested", payload=requested("session:other"))
        yield EventFrame(event="exec.approval.requested", payload=requested())
        await accepted.wait()
        yield EventFrame(event="chat", payload={"state": "final", "text": "done"})
    async def resolve(**kwargs):
        accepted.set()
        return {"success": True, "payload": {}}
    port = Mock(chat_stream=frames, exec_approval_resolve=AsyncMock(side_effect=resolve))
    chat = OpenClawChatAdapter(port)
    monkeypatch.setattr("engine.community.core.adapters.openclaw.chat.IntentEvalObserver", Mock())
    monkeypatch.setattr(bridge, "_chat_plugin", lambda: chat)
    kernel = A2aKernel()
    monkeypatch.setattr(R, "_kernel", kernel)
    rid, _ = await kernel.open_run("session:one", "key", "hello", "digest")
    task = asyncio.create_task(bridge.execute_and_stream(kernel, rid, "session:one", "hello"))
    for _ in range(100):
        if kernel.interaction_pending(rid, "approval-1"):
            break
        await asyncio.sleep(0.001)
    try:
        assert kernel.interaction_pending(rid, "approval-1")
        assert R._canonical_context("ResolveInteraction", {
            "interactionId": "approval-1", "resolution": {"decision": "deny"}}, 1)[0] == "session:one"
        assert exec_resolve_supported()
        assert not R._engine_resolve_supported()
        result = await R._resolve_interaction({"interactionId": "approval-1",
            "resolution": {"kind": "exec", "decision": "allow-once"}}, 1, None)
        assert result["interaction"]["resolved"] is True
        await asyncio.wait_for(task, 1)
        assert kernel.runs[rid].state == "TASK_STATE_COMPLETED"
        assert not chat._a2a_approvals.pending
        port.exec_approval_resolve.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_scoped_card_does_not_claim_full_resolve():
    card = make_agent_card("http://example", exec_resolve_supported=True)
    urls = [ext["url"] for ext in card["capabilities"]["extensions"]]
    assert OPENCLAW_EXEC_RESOLVE_URI in urls
    assert "https://avernet.local/a2a/ext/resolve/v1" not in urls


async def test_canceling_queued_a2a_stream_does_not_clear_active_approval(monkeypatch):
    blocked = asyncio.Event()
    async def frames(**kwargs):
        if kwargs["message"] == "first":
            yield EventFrame(event="exec.approval.requested", payload=requested())
        else:
            blocked.set()
        await asyncio.Event().wait()
    port = Mock(chat_stream=frames)
    chat = OpenClawChatAdapter(port)
    monkeypatch.setattr("engine.community.core.adapters.openclaw.chat.IntentEvalObserver", Mock())
    first = chat.stream_for_a2a(ChatRequest(userId="", agentId="", sessionId="session:one", query="first"))
    await anext(first)
    second = chat.stream_for_a2a(ChatRequest(userId="", agentId="", sessionId="session:one", query="second"))
    waiter = asyncio.create_task(anext(second))
    await blocked.wait()
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert "approval-1" in chat._a2a_approvals.pending
    await first.aclose()
    assert not chat._a2a_approvals.pending
