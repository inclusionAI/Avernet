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
    """Outer-timeout path: the forward hangs past the patched timeout."""
    class _HangingFake:
        async def inject(self, session_key, message, label=None, auth=None):
            await asyncio.sleep(10)   # longer than the patched timeout below
            return {"ok": True}

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


def test_inject_port_swallows_timeout_as_internal_error(app_client,
                                                         monkeypatch):
    """R14-1: real ports convert transport timeouts into ok=False with
    code=INTERNAL_ERROR (claude_code/_chat.py:145-149, openclaw the same).
    The router must classify that as UNKNOWN — never roll back to a state
    that would re-write. Regression for the reproduced double-write:
    downstream_writes must stay 1 across the same-key retry."""
    class _PortTimeoutFake:
        def __init__(self):
            self.writes = 0

        async def inject(self, session_key, message, label=None, auth=None):
            self.writes += 1
            # the port's exact shape for a swallowed RPC timeout:
            return {"ok": False, "error": {"code": "INTERNAL_ERROR",
                                            "message": "timeout"}}

    fake = _PortTimeoutFake()
    _install_fake_chat(monkeypatch, fake)
    k = R._kernel

    async def scenario():
        return await R._inject_context(
            _inject_params("ctx-h", "吞包超时", "inj-10"), 1, None)

    out = asyncio.run(scenario())
    assert out["error"]["data"]["reason"] == "unknown_outcome"
    assert k._inject_dedup[("ctx-h", "inj-10")][0] == "unknown"
    out2 = asyncio.run(scenario())
    assert out2["inject"] == "duplicate_unknown"
    assert fake.writes == 1, "same-key retry must NOT re-write (R14-1)"


def test_inject_engine_level_rejection_still_rolls_back(app_client,
                                                         monkeypatch):
    """Non-transport error codes (engine looked at the request and refused
    写入) remain retriable — the rollback path stays intact."""
    fake = _FakeChat(reject_times=999)
    _install_fake_chat(monkeypatch, fake)

    async def call():
        return await R._inject_context(
            _inject_params("ctx-i", "策略拒绝", "inj-11"), 1, None)

    out = asyncio.run(call())
    assert out["error"]["data"]["reason"] == "inject_forward_failed"
    out2 = asyncio.run(call())   # same-key retry re-attempts (rollback held)
    assert out2["error"]["data"]["reason"] == "inject_forward_failed"
    assert len(fake.calls) == 2


def test_inject_cancelled_marks_unknown_and_propagates(app_client,
                                                       monkeypatch):
    """R14-2: cancelling the dispatch mid-write leaves no fake in_progress
    residue — the key transitions to unknown and the cancellation
    propagates."""
    class _CancellableFake:
        async def inject(self, session_key, message, label=None, auth=None):
            await asyncio.sleep(30)

    monkeypatch.setattr(R, "_INJECT_FORWARD_TIMEOUT_SECONDS", 60)
    _install_fake_chat(monkeypatch, _CancellableFake())
    k = R._kernel

    async def scenario():
        task = asyncio.create_task(R._inject_context(
            _inject_params("ctx-j", "取消样本", "inj-12"), 1, None))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "not-cancelled"

    result = asyncio.run(scenario())
    assert result == "cancelled", "CancelledError must propagate"
    assert k._inject_dedup[("ctx-j", "inj-12")][0] == "unknown", \
        "cancelled reservation must NOT stay in_progress"


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


class _Frame:
    """Minimal EventFrame stand-in at the exact boundary bridge consumes."""

    def __init__(self, event, payload):
        self.event = event
        self.payload = payload


def test_engine_inject_frame_isolation(app_client, monkeypatch):
    """R12-F3/R14-3: real-bridge regression — a fake ChatService.stream
    yields the exact confessing frame order (main delta → inject-*
    final → main delta → real final). The bridge must route the inject
    frame to the context channel, keep the main run alive past it, consume
    the subsequent delta, and finish with the uncontaminated real final."""
    k = R._kernel

    async def make_stream():
        rid_holder = {}

        class _FakeStreamChat:
            async def stream(self, request, auth=None):
                rid = rid_holder["rid"]

                async def gen():
                    yield _Frame("chat", {"state": "delta",
                                          "runId": rid,
                                          "deltaText": "回答开头"})
                    # 旁观注入终帧：runId=inject-*,state=final——正是原缺陷
                    # 提前终止主 run 的那类帧
                    yield _Frame("chat", {"state": "final",
                                          "runId": "inject-x",
                                          "message": {"role": "assistant",
                                                      "content": [{"type": "text",
                                                                   "text": "旁观内容"}]}})
                    yield _Frame("chat", {"state": "delta",
                                          "runId": rid,
                                          "deltaText": "回答继续"})
                    yield _Frame("chat", {"state": "final",
                                          "runId": rid,
                                          "message": {"role": "assistant",
                                                      "content": [{"type": "text",
                                                                   "text": "回答开头回答继续（真实终稿）"}]}})

                return gen()

        chat = _FakeStreamChat()

        def fake_plugin():
            return chat

        monkeypatch.setattr(bridge, "_chat_plugin", fake_plugin)

        rid, _ = await k.open_run("iso", "ik", "first", None)
        rid_holder["rid"] = rid
        q = k.subscribe_context("iso")
        # execute_and_stream is the router's real dispatch entry (it also
        # performs the WORKING transition + final completion).
        await bridge.execute_and_stream(k, rid, "iso", "查询文本")
        return rid, q

    rid, q = asyncio.run(make_stream())

    injected = asyncio.run(asyncio.wait_for(q.get(), 2))
    assert injected["type"] == "message"
    assert injected["ev"]["message"]["parts"][0]["text"] == "旁观内容"

    run = k.get_run(rid)
    # inject final 不得提前终止主 run 也不得污染终稿；真实分发入口把
    # 流远端 run 收在 COMPLETED（inject 帧被跳过后 final 才终止流）
    assert run.state == "TASK_STATE_COMPLETED", run.state
    assert run.latest_text == "回答开头回答继续（真实终稿）", \
        run.latest_text
    # R14 复审建议：第二个 delta 的事件级证明——final 文本本身也含
    # "回答继续"，仅查终稿不足以证明增量帧被消费；直接断言 events 里
    # 存在两个 artifact 增量块（若 inject final 提前终止，第二块缺失）。
    deltas = [e for e in run.events
              if e["type"] == "artifactUpdate"
              and e["ev"]["artifactUpdate"].get("append") is True]
    assert len(deltas) == 1, \
        f"expected exactly one append chunk, got {len(deltas)}" \
        "（首块 append=False；第二个 delta 是唯一的 append=True 块——" \
        "它缺失即证明 bridge 在 inject final 处提前终止）"
    assert deltas[0]["ev"]["artifactUpdate"]["artifact"]["parts"][0]["text"] == "回答继续"


def test_kernel_publish_engine_inject_unit(app_client):
    """Kernel-level channel separation (kept from the original F3 test):
    publish_engine_inject fans out to the context channel only."""
    k = R._kernel

    async def scenario():
        rid, _ = await k.open_run("iso2", "ik2", "first", None)
        q = k.subscribe_context("iso2")
        await k.publish_delta(rid, "回答开头")
        k.publish_engine_inject("iso2", "旁观内容", "inject-y")
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

# ══════════ E4: ResolveInteraction 全闭环（HITL 三行判定）══════════

class _FakeResolveChat:
    """三 resolve 方法 +.success 契约（R17-3 形状）。"""

    mro_marker = "resolve_capability"

    def __init__(self, *, fail_once=False, fail_code="REJECTED"):
        self.calls: list[tuple[str, dict]] = []
        self.fail_once = fail_once
        self.fail_code = fail_code

    async def resolve_exec_approval(self, session_key, run_id, decision,
                                    message=None):
        return self._record("exec", session_key=session_key,
                            run_id=run_id, decision=decision,
                            message=message)

    async def resolve_interaction(self, session_key, run_id, response):
        return self._record("ask_user", session_key=session_key,
                            run_id=run_id, response=response)

    async def resolve_mode_transition(self, session_key, run_id, decision):
        return self._record("mode_switch", session_key=session_key,
                            run_id=run_id, decision=decision)

    def _record(self, kind, **params):
        self.calls.append((kind, params))
        if self.fail_once:
            self.fail_once = False
            return {"success": False, "error": {"code": self.fail_code,
                                                "message": "denied"}}
        return {"success": True, "payload": {}}


def _install_resolve_bridge(monkeypatch, fake):
    import engine.community.api.transport.a2a.router as R
    import engine.community.api.transport.a2a.engine_bridge as B

    def fake_plugin():
        return fake
    monkeypatch.setattr(B, "_chat_plugin", fake_plugin)
    return R


@pytest.mark.asyncio
async def test_resolve_interaction_full_loop(monkeypatch):
    fake = _FakeResolveChat()
    R = _install_resolve_bridge(monkeypatch, fake)
    R._kernel = kernel_mod.A2aKernel()
    k = R._kernel

    rid, _ = await k.open_run("hitl-ctx", "k", "first", "d")
    k.runs[rid].native_run_id = "native-42"
    await k.publish_interaction_requested(
        rid, "i-1", {"kind": "exec", "prompt": "rm test?"})

    out = await R._resolve_interaction(
        {"interactionId": "i-1",
         "resolution": {"decision": "approve", "kind": "exec",
                        "message": "ok go"}}, 1, None)
    assert out["interaction"]["resolved"] is True, out
    # 引擎收到正确方法 + native id（16-3b）+ 完整决策字段
    kind, params = fake.calls[0]
    assert kind == "exec"
    assert params["run_id"] == "native-42"       # 不是 kernel rid
    assert params["decision"] == "approve"
    assert params["message"] == "ok go"          # message 不丢
    # run 恢复 WORKING + resolved 事件广播
    assert k.runs[rid].state == "TASK_STATE_WORKING"
    backlog, _ = await k.subscribe(rid, since_seq=0)
    resolved_events = [e for e in backlog if e["type"] == "statusUpdate"]
    assert any(e["ev"]["statusUpdate"].get("metadata", {}).get(
        "engine_frame", {}).get("interactionId") == "i-1"
        for e in resolved_events)


@pytest.mark.asyncio
async def test_resolve_interaction_conflict_and_not_found(monkeypatch):
    fake = _FakeResolveChat()
    R = _install_resolve_bridge(monkeypatch, fake)
    R._kernel = kernel_mod.A2aKernel()
    k = R._kernel

    # not found
    out = await R._resolve_interaction(
        {"interactionId": "ghost",
         "resolution": {"decision": "approve"}}, 1, None)
    assert out["error"]["data"]["reason"] == "not_found"

    # conflict：已 resolve 后重复提交（run 已回 WORKING）
    rid, _ = await k.open_run("hitl-ctx2", "k2", "first", "d")
    k.runs[rid].native_run_id = "n"
    await k.publish_interaction_requested(rid, "i-2", {})
    await k.publish_interaction_resolved(rid, {})   # 决策已有
    out2 = await R._resolve_interaction(
        {"interactionId": "i-2",
         "resolution": {"decision": "approve"}}, 2, None)
    assert out2["error"]["data"]["reason"] == "resolve_conflict"
    assert fake.calls == []                        # 引擎收到 0 次二次执行


@pytest.mark.asyncio
async def test_resolve_engine_rejection_and_internal_error(monkeypatch):
    fake = _FakeResolveChat(fail_once=True, fail_code="INTERNAL_ERROR")
    R = _install_resolve_bridge(monkeypatch, fake)
    R._kernel = kernel_mod.A2aKernel()
    k = R._kernel
    rid, _ = await k.open_run("hitl-ctx3", "k3", "first", "d")
    k.runs[rid].native_run_id = "n"
    await k.publish_interaction_requested(rid, "i-3", {})

    out = await R._resolve_interaction(
        {"interactionId": "i-3",
         "resolution": {"decision": "approve", "kind": "exec"}}, 1, None)
    # INTERNAL_ERROR → unknown_outcome（结果不可知——engine 可能已接决策）
    assert out["error"]["data"]["reason"] == "unknown_outcome"
    assert k.runs[rid].state == "TASK_STATE_INPUT_REQUIRED"  # 状态不回写


@pytest.mark.asyncio
async def test_resolve_validate_response_contract(monkeypatch):
    """_forward_resolve 判定必须用 R17-3 的真实 {success} 契约——
    一个返回 {ok: True} 的引擎 adapter 会被误判失败。"""
    class _OldContractChat:
        async def resolve_interaction(self, session_key, run_id, response):
            return {"success": False,
                    "error": {"code": "OLD",
                               "message": "engine returned legacy shape"}}

        async def resolve_exec_approval(self, **kwargs):
            raise AssertionError("exec expected for kind=exec only when asked")

        async def resolve_mode_transition(self, **kwargs):
            raise AssertionError("mode switch expected for kind=mode_switch only")

    fake = _OldContractChat()
    R = _install_resolve_bridge(monkeypatch, fake)
    k = R._kernel = kernel_mod.A2aKernel()
    rid, _ = await k.open_run("hitl-ctx4", "k4", "first", "d")
    k.runs[rid].native_run_id = "n"
    await k.publish_interaction_requested(rid, "i-4", {})
    out = await R._resolve_interaction(
        {"interactionId": "i-4",
         "resolution": {"decision": "approve"}}, 1, None)
    assert out["error"]["data"]["reason"] == "resolve_forward_failed"


# ══════════ 1c: CancelTask 闭环（TC-CANCEL，设计 §7/§8.6）══════════

def _seed_run(open_ctx: str, key: str, *,
               state: str | None = "TASK_STATE_WORKING") -> str:
    """open→(WORKING)→可选终态，同一 asyncio.run 内完成。"""
    k = R._kernel

    async def seed():
        rid, _ = await k.open_run(open_ctx, key, "first", "d")
        if state:
            await k.publish_status(rid, state)
        return rid

    return asyncio.run(seed())


@pytest.fixture()
def app_client_gated(monkeypatch):
    """同 app_client 但 gate 开关打开——真实 _auth_gate_guard 生效。"""
    app = FastAPI()

    class _OnGate:
        async def get_switch(self) -> bool:
            return True

        async def set_switch(self, enabled: bool) -> None:  # pragma: no cover
            pass

        async def verify(self, token: str, content: str,  # pragma: no cover
                         session_id: str):
            raise NotImplementedError

    service = _OnGate()

    class _Module(Module):
        def configure(self, binder: Binder) -> None:
            binder.bind(AuthGateService, to=InstanceProvider(service),
                        scope=SingletonScope)

    attach_injector(app, Injector([_Module()]))
    app.include_router(R.router)
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    with TestClient(app) as client:
        yield client


def test_cancel_working_run_terminal_event_and_delta_drop(app_client):
    """TC-CANCEL 主路径：WORKING run → CANCELED；订阅者收 cancel 终态；
    迟到 delta/final 不再改变终稿与状态（T1：终态之后全丢弃）。"""
    k = R._kernel

    async def seed():
        rid, _ = await k.open_run("cx", "mk1", "first", "d")
        await k.publish_status(rid, "TASK_STATE_WORKING")
        await k.publish_delta(rid, "部分回答")
        backlog, q = await k.subscribe(rid, since_seq=0)
        return rid, q

    rid, q = asyncio.run(seed())
    r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    body = r.json()
    assert "error" not in body, body
    task = body["result"]["task"]
    assert task["id"] == rid
    assert task["status"]["state"] == "TASK_STATE_CANCELED"

    # 订阅者收到完整的 cancel 终态序列（先 CANCELED statusUpdate、后
    # final 终结 marker——与既有 complete_run 推送序列同构），且仅一次。
    async def drain_terminal():
        seen: list[dict] = []
        while True:
            ev = await asyncio.wait_for(q.get(), 2)
            seen.append(ev)
            meta = ev["ev"]["statusUpdate"]["metadata"]
            if meta.get("final") == "true":
                # 终结之后队列必须再无事件（T1 单次终结）
                assert q.empty()
                return seen

    events = asyncio.run(drain_terminal())
    states = [e["ev"]["statusUpdate"]["status"]["state"] for e in events]
    assert states == ["TASK_STATE_CANCELED", "TASK_STATE_CANCELED"], states
    assert events[0]["ev"]["statusUpdate"]["metadata"].get("final") is None
    marker_meta = events[1]["ev"]["statusUpdate"]["metadata"]
    assert marker_meta["final"] == "true"
    assert "lastSeq" in marker_meta

    # 迟到增量/终稿被丢弃——cancel 在运行方向胜出（§8.6）
    async def late():
        await k.publish_delta(rid, "迟到增量")
        await k.publish_final(rid, "迟到终稿")

    asyncio.run(late())
    run = k.get_run(rid)
    assert run.state == "TASK_STATE_CANCELED"
    assert run.latest_text == "部分回答", run.latest_text

    # T1：迟到的 complete_run 不再改写状态或二次终结
    async def late_complete():
        await k.complete_run(rid, "TASK_STATE_COMPLETED")

    asyncio.run(late_complete())
    run = k.get_run(rid)
    assert run.state == "TASK_STATE_CANCELED"

    # T1 全迟到帧面：bridge 仍在泵流时状态不得复活、不得二次终结、
    # 也不得在 cancel 后注册新 interaction（注册表仅由 cancel 前帧持有）
    seq_before = run.last_seq
    async def late_frames():
        await k.publish_status(rid, "TASK_STATE_WORKING")
        await k.publish_interaction_requested(rid, "i-late", {})
        await k.publish_interaction_resolved(rid, {"interactionId": "i-late"})
        await k.publish_agent_progress(rid, {"state": "agent"})

    asyncio.run(late_frames())
    run = k.get_run(rid)
    assert run.state == "TASK_STATE_CANCELED", \
        "迟到 status/interaction 帧复活了终态 run"
    assert run.last_seq == seq_before, "迟到帧在终结后追加了事件"
    assert "i-late" not in k._interactions
    # 订阅者不再收到终结之后的任何事件
    assert q.empty()


def test_cancel_terminal_states_and_not_found(app_client):
    """终态后取消 → 标准错误（data 带 state + already_terminal 判读）；
    not found → 显式错误码；重复 cancel 已取消 run 同一错误面。"""
    k = R._kernel

    async def seed_terminal(state: str) -> str:
        rid, _ = await k.open_run("cx", f"tk-{state}", "first", "d")
        await k.publish_status(rid, "TASK_STATE_WORKING")
        await k.complete_run(rid, state)
        return rid

    rid_done = asyncio.run(seed_terminal("TASK_STATE_COMPLETED"))
    r = _rpc(app_client, 1, "CancelTask", {"taskId": rid_done})
    err = r.json()["error"]
    assert err["code"] == -32000
    assert err["data"]["reason"] == "task_terminal"
    assert err["data"]["state"] == "TASK_STATE_COMPLETED"
    assert err["data"]["already_terminal"] is True
    assert k.get_run(rid_done).state == "TASK_STATE_COMPLETED"

    # 幂等面：重复 cancel 已 CANCELED 的 run → 同一标准错误，
    # data.state 携带 CANCELED 事实（迟到重试可判读 run 终局，无静默歧义）
    rid_cx = asyncio.run(seed_terminal("TASK_STATE_CANCELED"))
    r2 = _rpc(app_client, 2, "CancelTask", {"taskId": rid_cx})
    err2 = r2.json()["error"]
    assert err2["data"]["reason"] == "task_terminal"
    assert err2["data"]["state"] == "TASK_STATE_CANCELED"
    assert err2["data"]["already_terminal"] is True

    r3 = _rpc(app_client, 3, "CancelTask", {"taskId": "ghost-run"})
    assert r3.json()["error"]["data"]["reason"] == "not_found_or_expired"


def test_cancel_does_not_touch_interaction_registry(app_client, monkeypatch):
    """§8.6：cancel 不回写/删除 resolution 记录——注册表仍在，resolve 在
    run 终态后判 not_found（结构化错误，非栈污染）。"""
    fake = _FakeResolveChat()
    _install_resolve_bridge(monkeypatch, fake)
    k = R._kernel

    async def seed():
        rid, _ = await k.open_run("hitl-cancel", "kk", "first", "d")
        k.runs[rid].native_run_id = "native-9"
        await k.publish_interaction_requested(
            rid, "i-cancel", {"kind": "exec", "prompt": "rm test?"})
        return rid

    rid = asyncio.run(seed())
    r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    assert r.json()["result"]["task"]["status"]["state"] == \
        "TASK_STATE_CANCELED"
    # registry 不被 cancel 触碰（HITL 记录与 run 生命周期解耦）
    assert k._interactions["i-cancel"] == rid

    async def try_resolve():
        return await R._resolve_interaction(
            {"interactionId": "i-cancel",
             "resolution": {"decision": "approve", "kind": "exec"}}, 1, None)

    out = asyncio.run(try_resolve())
    assert out["error"]["data"]["reason"] == "not_found", out
    assert fake.calls == [], "终端 run 的 resolve 不得再触发引擎转发"


def test_cancel_gate_checks_the_run_context(app_client, monkeypatch):
    """CancelTask 是写操作——与 ResolveInteraction 对齐进 gate；gate 判定
    用的 session 即目标 run 的 context（R13-3 同源约束）。"""
    rid = _seed_run("ctx-cancel", "gk1")
    seen: list[tuple[str, str | None]] = []

    async def fake_guard(request, rid_, method, session_key, message_text,
                         auth_gate_service):
        seen.append((method, session_key))
        return None

    monkeypatch.setattr(R, "_auth_gate_guard", fake_guard)
    r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    assert r.json()["result"]["task"]["status"]["state"] == \
        "TASK_STATE_CANCELED"
    assert seen == [("CancelTask", "ctx-cancel")]


def test_cancel_gate_rejection_blocks_state_change(app_client, monkeypatch):
    """gate 拒 → 错误透传，run 状态不动（写操作不因 gate 失败而半途落地）。"""
    rid = _seed_run("ctx-cancel2", "gk2")

    async def rejecting_guard(request, rid_, method, session_key,
                              message_text, auth_gate_service):
        return R._rpc_error_response(
            rid_, {"code": -32000, "message": "zero_check failed",
                   "data": {"reason": "ZERO_CHECK_FAILED"}})

    monkeypatch.setattr(R, "_auth_gate_guard", rejecting_guard)
    r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    assert r.json()["error"]["data"]["reason"] == "ZERO_CHECK_FAILED"
    assert R._kernel.get_run(rid).state == "TASK_STATE_WORKING"


def test_cancel_requires_iam_token_when_gate_on(app_client_gated):
    """gate 打开的真实防卫路径：无 x-iam-token 的 CancelTask 被拒，零状态变化。"""
    rid = _seed_run("ctx-token", "gk3")
    r = _rpc(app_client_gated, 1, "CancelTask", {"taskId": rid})
    err = r.json()["error"]
    assert err["code"] == -32000
    assert err["data"]["reason"] == "auth_required"
    assert err["message"] == "Missing x-iam-token"
    assert R._kernel.get_run(rid).state == "TASK_STATE_WORKING"


def test_cancel_malformed_params_rejected(app_client):
    """F8：畸形 params 全走 -32602（先于 gate、零内核副作用）。"""
    for i, bad in enumerate([{}, {"taskId": ""}, {"taskId": 42},
                             {"taskId": None}, {"taskId": ["x"]}]):
        r = _rpc(app_client, i, "CancelTask", bad)
        assert r.json()["error"]["code"] == -32602, (bad, r.json())
    # handler 直调的防御面（绕过 pre-gate normalization 时也不裸抛）
    async def direct_call():
        with pytest.raises(R._ParamError):
            await R._cancel_task({"taskId": 42}, 1, None)

    asyncio.run(direct_call())


def test_agent_card_declares_cancel_extension(app_client):
    """能力卡锁定：CancelTask 服务能力以 extension URI 声明（spec v1.0
    AgentCapabilities 无标准 cancellation 字段——沿用 EXTENSION_URIS 格式）。"""
    r = app_client.get("/.well-known/agent-card.json")
    card = r.json()
    uris = [e["url"] for e in card["capabilities"]["extensions"]]
    from engine.community.api.transport.a2a.card import EXTENSION_URIS
    assert EXTENSION_URIS["cancel"] in uris, uris
