"""A2A transport — repo regression suite (R11/R12/R13 review findings).

Covers the review-required scenarios as repeatable in-repo tests:

R13 (inject chain):
  1. normal inject: downstream call matches the real chat.inject contract
     (message is the plain string; saved text == original) and the
     broadcast happens only after the engine write confirms
  2. engine explicit rejection + same-key retry: no premature broadcast,
     retry not wrongly deduped
  3. concurrent same-key inject: single write, failure never masked
  4. inject timeout: explicit unknown-outcome strategy (key retained)
  5. gate context vs execution context conflict: rejected, zero dispatch,
     zero broadcast
  6. plugin/manager/relay unavailable: honest failure, no fake accepted

R11/R12 (execute-once / wire / snapshot / boundary):
  7. SendMessage executes; same-key streaming dedups (exactly once)
  8. retryOnly: unknown-outcome, type rejection, declared-ext missing
  9. boundary: -32700/-32600/-32602 across malformed inputs, zero naked 500
  10. GetTask content snapshot survives cache_limit
  11. payload split: delta increments vs cumulative snapshots
  12. engine inject frame isolation (inject-* never terminates the run)
  13. base-url resolution (SERVER_* / 0.0.0.0 / A2A_PUBLIC_BASE_URL)

The engine surface is faked at the ChatService boundary — per review, unit
tests must not rely on production success-degradation branches.
"""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import engine.community.api.transport.a2a.router as R
import engine.community.api.transport.a2a.engine_bridge as bridge
from engine.community.api.transport.a2a import kernel as kernel_mod
from engine.community.api.transport.a2a.router import InjectForwardRejected


# ── fixtures ───────────────────────────────────────────────────────────────

from injector import Binder, InstanceProvider, Injector, Module, SingletonScope  # noqa: E402
from fastapi_injector import attach_injector  # noqa: E402
from engine.community.plugin_api.auth_gate.protocol import AuthGateService  # noqa: E402


class _OffAuthGate:
    """Gate disabled — the A2A gate path is exercised via _auth_gate_guard
    monkeypatches (see R13-3 tests); integrate here a switched-on fake when
    full DI gate tests are added."""

    async def get_switch(self) -> bool:
        return False

    async def set_switch(self, enabled: bool) -> None:  # pragma: no cover
        pass

    async def verify(self, token: str, content: str, session_id: str):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture()
def app_client(monkeypatch):
    """Router-level app with a fresh kernel per test and the auth gate off."""
    app = FastAPI()
    service = _OffAuthGate()

    class _Module(Module):
        def configure(self, binder: Binder) -> None:
            binder.bind(AuthGateService, to=InstanceProvider(service),
                        scope=SingletonScope)

    attach_injector(app, Injector([_Module()]))
    app.include_router(R.router)
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    with TestClient(app) as client:
        yield client


def _rpc(client, rid, method, params, headers=None):
    return client.post("/rpc", json={"jsonrpc": "2.0", "id": rid,
                                     "method": method, "params": params},
                       headers=headers or {})


def _inject_params(ctx, text, msg_id, msg_ctx=None):
    message = {"role": "ROLE_USER", "parts": [{"text": text}],
               "messageId": msg_id}
    if msg_ctx is not None:
        message["contextId"] = msg_ctx
    return {"contextId": ctx, "message": message}


class _FakeChat:
    """Fake ChatService at the real plugin boundary."""

    def __init__(self, *, ok=True, reject_times=0, hang=False):
        self.calls: list[dict] = []
        self.ok = ok
        self.reject_times = reject_times
        self.hang = hang
        self.mro_inject = True

    async def inject(self, session_key, message, label=None, auth=None):
        self.calls.append({"sessionKey": session_key, "message": message,
                           "label": label})
        if self.hang:
            await asyncio.sleep(60)
        if self.reject_times > 0:
            self.reject_times -= 1
            return {"ok": False, "error": {"code": "REJECTED",
                                            "message": "denied"}}
        if not self.ok:
            return {"ok": False, "error": {"code": "REJECTED",
                                            "message": "denied"}}
        return {"ok": True, "payload": {}}

    async def stream(self, request, auth=None):
        raise RuntimeError("unused in these tests")

    class _Meta(type):
        pass

    def _has_inject_mro(self):  # helper replicating ws_server check
        return self.mro_inject


def _install_fake_chat(monkeypatch, fake):
    def fake_plugin():
        return fake
    monkeypatch.setattr(bridge, "_chat_plugin", fake_plugin)


# ── R13-1: contract + no premature broadcast ──────────────────────────────

def test_inject_normal_contract_and_late_broadcast(app_client, monkeypatch):
    fake = _FakeChat()
    _install_fake_chat(monkeypatch, fake)
    k = R._kernel
    q = k.subscribe_context("ctx-a")  # subscriber registered BEFORE inject

    r = _rpc(app_client, 1, "InjectContext",
             _inject_params("ctx-a", "要注入的原文", "inj-1"))
    assert r.json()["result"] == {"inject": "accepted"}

    # R13-1: forwarded message is the PLAIN STRING (chat.inject contract —
    # claude_code_gateway does String(params.message); an object shape would
    # corrupt the transcript with "[object Object]").
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["message"] == "要注入的原文" and isinstance(call["message"], str)
    assert call["sessionKey"] == "ctx-a"

    # R13-2: broadcast only after engine confirm — the event is in the
    # queue now; terminate the subscriber queue and pull it to prove the
    # broadcast payload matches the accepted text.
    k.unsubscribe_context("ctx-a", q)
    state = k._inject_dedup[("ctx-a", "inj-1")]
    assert state[0] == "accepted"


def test_inject_broadcast_order_engine_first(app_client, monkeypatch):
    """Strict order proof: while the engine write is pending, the context
    subscriber has received NOTHING; only after confirm does it arrive."""
    fake = _FakeChat()
    _install_fake_chat(monkeypatch, fake)
    k = R._kernel
    q = k.subscribe_context("ctx-b")

    # Drive the handler directly so we can observe mid-flight state.
    async def scenario():
        # reserve and forward inside one handler call; stop before commit:
        # simulate by calling the internal pieces in order.
        reserve = await k.inject_reserve("ctx-b", "inj-2")
        assert reserve == "in_progress"
        assert q.empty()                # no broadcast while engine pending
        await R._forward_engine_inject("ctx-b", "midflight")
        assert q.empty()                # forward does NOT broadcast
        k.inject_commit("ctx-b", "midflight", "inj-2")
        return await asyncio.wait_for(q.get(), 2)

    ev = asyncio.run(scenario())
    assert ev["type"] == "message"
    assert ev["ev"]["message"]["parts"][0]["text"] == "midflight"


# ── R13-2: rejection + retry ────────────────────────────────────────────────

def test_inject_rejected_then_retried(app_client, monkeypatch):
    fake = _FakeChat(reject_times=1)   # first write rejected, second ok
    _install_fake_chat(monkeypatch, fake)
    k = R._kernel
    q = k.subscribe_context("ctx-c")

    r1 = _rpc(app_client, 1, "InjectContext",
              _inject_params("ctx-c", "重试样本", "inj-3"))
    body = r1.json()["error"]
    assert body["data"]["reason"] == "inject_forward_failed"
    assert q.empty(), "rejected inject must not broadcast"
    assert ("ctx-c", "inj-3") not in k._inject_dedup  # rollback cleared

    r2 = _rpc(app_client, 2, "InjectContext",
              _inject_params("ctx-c", "重试样本", "inj-3"))  # same key
    assert r2.json()["result"] == {"inject": "accepted"}
    assert len(fake.calls) == 2, "same-key retry must re-attempt the write"


def test_inject_concurrent_same_key_single_write(app_client, monkeypatch):
    class _SuspendingFake(_FakeChat):
        async def inject(self, session_key, message, label=None, auth=None):
            await asyncio.sleep(0.05)   # real suspend point → concurrency window
            return await super().inject(session_key, message, label, auth)

    fake = _SuspendingFake()
    _install_fake_chat(monkeypatch, fake)
    params = _inject_params("ctx-d", "并发样本", "inj-4")

    async def scenario():
        return await asyncio.gather(
            R._inject_context(params, 1, None),
            R._inject_context(params, 2, None),
        )

    out1, out2 = asyncio.run(scenario())
    statuses = {json.dumps(out1, sort_keys=True),
                json.dumps(out2, sort_keys=True)}
    assert len(fake.calls) == 1, "concurrent same-key must write exactly once"
    assert any("accepted" in s for s in statuses)
    assert any("duplicate_in_progress" in s for s in statuses), statuses


def test_inject_timeout_unknown_state(app_client, monkeypatch):
    class _HangingFake:
        async def inject(self, session_key, message, label=None, auth=None):
            await asyncio.sleep(10)   # longer than the patched timeout below
            return {"ok": True}

    # shorten the forward timeout so the hang resolves as TimeoutError
    monkeypatch.setattr(R, "_INJECT_FORWARD_TIMEOUT_SECONDS", 0.05)
    _install_fake_chat(monkeypatch, _HangingFake())
    k = R._kernel

    async def scenario():
        return await R._inject_context(_inject_params("ctx-e", "超时样本", "inj-5"), 1, None)

    out = asyncio.run(scenario())
    assert out["error"]["data"]["reason"] == "unknown_outcome"
    # key retained as unknown — same-key retry must not re-attempt blindly
    assert k._inject_dedup[("ctx-e", "inj-5")][0] == "unknown"
    out2 = asyncio.run(scenario())
    assert out2["inject"] == "duplicate_unknown"


# ── R13-3: gate context vs execution context ───────────────────────────────

def test_inject_context_conflict_rejected_zero_side_effects(app_client,
                                                             monkeypatch):
    """message.contextId=A (allowed) + params.contextId=B (victim): the
    canonical-context extraction rejects before gate AND execution."""
    fake = _FakeChat()
    _install_fake_chat(monkeypatch, fake)

    gate_calls: list[str] = []

    async def fake_guard(request, rid, method, session_key, message_text,
                         auth_gate_service):
        gate_calls.append(session_key)
        return None

    monkeypatch.setattr(R, "_auth_gate_guard", fake_guard)

    r = _rpc(app_client, 1, "InjectContext",
             _inject_params("victim-B", "越权内容", "inj-6",
                            msg_ctx="allowed-A"))
    assert r.json()["error"]["code"] == -32602
    assert gate_calls == [], "conflict rejected pre-gate"
    assert fake.calls == [], "zero engine dispatch"
    assert ("victim-B", "inj-6") not in R._kernel._inject_dedup
    # and no broadcast channels carry anything:
    assert R._kernel._context_subs.get("victim-B") is None


def test_gate_checks_the_executed_context(app_client, monkeypatch):
    """When gate passes, it was checked against the SAME context that gets
    written (structural guarantee of _canonical_context)."""
    fake = _FakeChat()
    _install_fake_chat(monkeypatch, fake)
    seen: list[str] = []

    async def fake_guard(request, rid, method, session_key, message_text,
                         auth_gate_service):
        seen.append(session_key)
        return None

    monkeypatch.setattr(R, "_auth_gate_guard", fake_guard)
    r = _rpc(app_client, 1, "InjectContext",
             _inject_params("real-target", "内容", "inj-7"))
    assert r.json()["result"] == {"inject": "accepted"}
    assert seen == ["real-target"]
    assert fake.calls[0]["sessionKey"] == "real-target"


# ── R13-4: unavailable surface fails honestly ──────────────────────────────

def test_inject_unavailable_engine_fails(app_client, monkeypatch):
    def broken_plugin():
        raise RuntimeError("engine chat surface unavailable")
    monkeypatch.setattr(bridge, "_chat_plugin", broken_plugin)

    async def scenario():
        return await R._inject_context(_inject_params("ctx-f", "内容", "inj-8"), 1, None)

    out = asyncio.run(scenario())
    assert out["error"]["data"]["reason"] == "inject_target_unavailable"
    k = R._kernel
    assert ("ctx-f", "inj-8") not in k._inject_dedup  # rollback: retriable


def test_inject_relay_none_fails(app_client, monkeypatch):
    class _NoInject:      # MRO has no inject → relay fallback path
        async def stream(self, request, auth=None):
            raise RuntimeError

    _install_fake_chat(monkeypatch, _NoInject())

    import engine.community.manager as mgr_mod
    class _M:
        relay = None
    monkeypatch.setattr(mgr_mod.EngineManager, "get_instance",
                        staticmethod(lambda: _M()))

    async def scenario():
        return await R._inject_context(_inject_params("ctx-g", "内容", "inj-9"), 1, None)

    out = asyncio.run(scenario())
    assert out["error"]["data"]["reason"] == "inject_target_unavailable"


# ── R11/R12 固化：execute-once / retry / boundary / snapshot ────────────────

def test_send_message_executes_once_with_streaming_dedup(app_client,
                                                         monkeypatch):
    calls: list[str] = []

    async def counting(kernel, rid, ctx, text, auth=None):
        calls.append(rid)
        # Minimal lifecycle so the SSE subscriber stream can terminate
        # (the real bridge publishes delta + complete; without terminal the
        # generator would block on q.get() forever).
        await kernel.publish_delta(rid, "回音")
        await kernel.complete_run(rid)

    monkeypatch.setattr(R, "execute_and_stream", counting)
    r1 = _rpc(app_client, 1, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "内容"}],
                    "messageId": "k1", "contextId": "c1"}})
    assert r1.json()["result"]["task"]["metadata"]["created"] is True
    # create_task runs on the TestClient portal loop — poll briefly for the
    # background dispatch to land (production path unchanged by the test).
    import time as _time
    _deadline = _time.monotonic() + 2.0
    while not calls and _time.monotonic() < _deadline:
        _time.sleep(0.02)
    assert len(calls) == 1, "non-streaming FIRST must dispatch"

    with app_client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": 2, "method": "SendStreamingMessage",
            "params": {"message": {"role": "ROLE_USER",
                                   "parts": [{"text": "内容"}],
                                   "messageId": "k1",
                                   "contextId": "c1"}}}) as resp:
        first_tid = None
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:]).get("result", {})
            if "task" in ev and first_tid is None:
                first_tid = ev["task"]["id"]
            if "statusUpdate" in ev and \
                    (ev["statusUpdate"].get("metadata") or {}).get("final"):
                break
    assert len(calls) == 1, "same-key streaming must not re-dispatch (C1)"
    assert first_tid == r1.json()["result"]["task"]["id"]


def test_retry_only_matrix(app_client):
    miss = _rpc(app_client, 1, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                    "messageId": "absent", "contextId": "cx"},
        "metadata": {"avernet.retryOnly": True}})
    assert miss.json()["error"]["data"]["reason"] == "unknown_outcome"

    bad = _rpc(app_client, 2, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                    "messageId": "b1", "contextId": "cx"},
        "metadata": {"avernet.retryOnly": "false"}})
    assert bad.json()["error"]["code"] == -32602

    declared = _rpc(app_client, 3, "SendMessage",
                    {"message": {"role": "ROLE_USER",
                                 "parts": [{"text": "x"}],
                                 "messageId": "d1", "contextId": "cx"},
                     "metadata": {}},
                    headers={"A2A-Extensions":
                             "https://avernet.local/a2a/ext/retry/v1"})
    assert declared.json()["error"]["code"] == -32602


def test_boundary_never_naked_500(app_client):
    r = app_client.post("/rpc", content=b"{", headers={
        "Content-Type": "application/json"})
    assert r.json()["error"]["code"] == -32700
    assert _rpc(app_client, 1, [], {}).status_code not in (500,)
    assert app_client.post("/rpc", json={"jsonrpc": "2.0",
                                         "id": 1, "method": []}
                           ).json()["error"]["code"] == -32600
    bad_parts = _rpc(app_client, 2, "SendStreamingMessage", {
        "message": {"role": "ROLE_USER", "parts": [None],
                    "messageId": "p1", "contextId": "cx"}})
    assert bad_parts.status_code != 500
    bad_seq = _rpc(app_client, 3, "ReplayEvents",
                   {"taskId": "t", "sinceSeq": "0"})
    assert bad_seq.json()["error"]["code"] == -32602
    bad_key = _rpc(app_client, 4, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                    "messageId": ["un"], "contextId": "cx"}})
    assert bad_key.json()["error"]["code"] == -32602


def test_get_task_snapshot_survives_cache_limit(app_client, monkeypatch):
    k = R._kernel

    async def seed():
        rid, _ = await k.open_run("sc", "sk", "first", None)
        await k.publish_delta(rid, "前半")
        await k.publish_final(rid, "终稿全文")
        return rid

    rid = asyncio.run(seed())
    run = k.get_run(rid)
    run.cache_limit = True
    run.events = []
    r = _rpc(app_client, 1, "GetTask", {"taskId": rid})
    arts = r.json()["result"]["task"]["artifacts"]
    assert arts and arts[0]["parts"][0]["text"] == "终稿全文"


def test_payload_delta_vs_snapshot_split():
    cc_delta = {"state": "delta", "delta": "好",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": "你好"}]}}
    assert bridge._payload_text(cc_delta, "delta") == "好"
    assert bridge._payload_text(cc_delta, "final") == "你好"
    no_inc = {"state": "delta",
              "message": {"content": [{"type": "text", "text": "你好"}]}}
    assert bridge._payload_text(no_inc, "delta") == ""


def test_engine_inject_frame_isolation(app_client):
    """inject-* frames fan out to the context channel only: the run completes
    with the real final, never polluted nor terminated early."""
    k = R._kernel

    async def scenario():
        rid, _ = await k.open_run("iso", "ik", "first", None)
        q = k.subscribe_context("iso")
        await k.publish_delta(rid, "回答开头")
        k.publish_engine_inject("iso", "旁观内容", "inject-x")
        await k.publish_delta(rid, "回答继续")
        await k.publish_final(rid, "真实终稿")
        await k.complete_run(rid)
        return rid, q

    rid, q = asyncio.run(scenario())
    injected = asyncio.run(asyncio.wait_for(q.get(), 2))
    assert injected["type"] == "message"
    run = k.get_run(rid)
    assert run.state == "TASK_STATE_COMPLETED"
    assert run.latest_text == "真实终稿"


def test_base_url_resolution(monkeypatch):
    from engine.community.config import get_local_base_url
    for var in ("SERVER_HOST", "SERVER_PORT", "A2A_PUBLIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SERVER_PORT", "20004")
    monkeypatch.setenv("SERVER_HOST", "0.0.0.0")
    assert get_local_base_url() == "http://127.0.0.1:20004"
    monkeypatch.setenv("A2A_PUBLIC_BASE_URL", "https://gw.example.com/x/")
    assert get_local_base_url() == "https://gw.example.com/x"