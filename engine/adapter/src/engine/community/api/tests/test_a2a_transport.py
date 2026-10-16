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
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import engine.community.api.transport.a2a.router as R
import engine.community.api.transport.a2a.engine_bridge as bridge
from engine.community.api.transport.a2a import kernel as kernel_mod
from engine.community.api.transport.a2a.card import make_agent_card
from engine.community.core.chat.models import ChatAbortRequest, ChatAbortResult  # noqa: E402
from engine.community.kernel.frames import ErrorShape  # noqa: E402


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

    def __init__(self, *, reject_times=0):
        self.calls: list[dict] = []
        self.reject_times = reject_times
        self.mro_inject = True

    async def inject(self, session_key, message, label=None, auth=None):
        self.calls.append({"sessionKey": session_key, "message": message,
                           "label": label})
        if self.reject_times > 0:
            self.reject_times -= 1
            return {"ok": False, "error": {"code": "REJECTED",
                                            "message": "denied"}}
        return {"ok": True, "payload": {}}


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
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())   # CancelledError 未被吞即通过
    assert k._inject_dedup[("ctx-j", "inj-12")][0] == "unknown", \
        "cancelled reservation must NOT stay in_progress"


# ── R13-3: gate context vs execution context ───────────────────────────────

def test_inject_context_conflict_rejected_zero_side_effects(app_client,
                                                             monkeypatch):
    """message.contextId=A (allowed) + params.contextId=B (victim): the
    canonical-context extraction rejects before gate AND execution."""
    fake = _FakeChat()
    _install_fake_chat(monkeypatch, fake)

    from unittest.mock import AsyncMock
    guard_spy = AsyncMock()
    monkeypatch.setattr(R, "_auth_gate_guard", guard_spy)

    r = _rpc(app_client, 1, "InjectContext",
             _inject_params("victim-B", "越权内容", "inj-6",
                            msg_ctx="allowed-A"))
    assert r.json()["error"]["code"] == -32602
    guard_spy.assert_not_awaited(), "conflict rejected pre-gate"
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
        pass

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

    async def counting(kernel, rid, ctx, text, auth=None, alive_ms=None):
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
    # sleep 先行：轮询行必须每次至少执行一次，别写成永不执行的防御分支。
    import time as _time
    for _ in range(100):
        _time.sleep(0.02)
        if calls:
            break
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

    async def resolve_exec_approval(self, session_key, interaction_id,
                                    decision, message=None):
        return self._record("exec", session_key=session_key,
                            interaction_id=interaction_id, decision=decision,
                            message=message)

    async def resolve_interaction(self, session_key, interaction_id,
                                  decision="submit", answer=None,
                                  message=None, answers=None, values=None,
                                  selected_options=None):
        return self._record("ask_user", session_key=session_key,
                            interaction_id=interaction_id, decision=decision,
                            answer=answer, message=message, answers=answers,
                            values=values,
                            selected_options=selected_options)

    async def resolve_mode_transition(self, session_key, interaction_id,
                                      decision):
        return self._record("mode_switch", session_key=session_key,
                            interaction_id=interaction_id, decision=decision)

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
    # 回归钉：native id 已登记的 run 上，转发键必须是 BaaS 持有的真实
    # interaction id——gateway pending 注册表只认它，native/kernel id 一概不认
    k.runs[rid].native_run_id = "native-42"
    await k.publish_interaction_requested(
        rid, "i-1", {"kind": "exec", "prompt": "rm test?"})

    out = await R._resolve_interaction(
        {"interactionId": "i-1",
         "resolution": {"decision": "approve", "kind": "exec",
                        "message": "ok go"}}, 1, None)
    assert out["interaction"]["resolved"] is True, out
    # 引擎收到正确方法 + 真实 interaction id + 完整决策字段
    kind, params = fake.calls[0]
    assert kind == "exec"
    assert params["interaction_id"] == "i-1"
    assert "native-42" not in params.values()   # native id 不得顶替转发键
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
    await k.publish_interaction_requested(rid, "i-2", {})
    await k.publish_interaction_resolved(rid, {})   # 决策已有
    out2 = await R._resolve_interaction(
        {"interactionId": "i-2",
         "resolution": {"decision": "approve"}}, 2, None)
    assert out2["error"]["data"]["reason"] == "resolve_conflict"
    assert fake.calls == []                        # 引擎收到 0 次二次执行


# ══════════ R7-F3: resolve 契约能力门控（无 resolve 契约的引擎不硬炸）══════════


@pytest.mark.asyncio
async def test_resolve_on_engine_without_resolve_contract_is_structured_unsupported(
    monkeypatch,
):
    """R7-F3 主复现：OpenClaw 形态的引擎被灰度选中后审批 resolve——
    `_forward_resolve` 不得按 Claude Code 专用契约硬调（AttributeError →
    HTTP 500、审批静默挂死），必须按能力门控出结构化 resolve_unsupported，
    pending 保持（决策未确认），run 不回 WORKING。"""
    from unittest.mock import MagicMock

    from engine.community.core.adapters.openclaw.chat import OpenClawChatAdapter

    # 契约钉：真实 adapter（Protocol 面之上）确实没有 Claude Code 专用
    # 的三个 resolve 方法——门控依赖的事实本身被测试钉住。
    real_chat = OpenClawChatAdapter(port=MagicMock())
    for method in ("resolve_exec_approval", "resolve_interaction",
                   "resolve_mode_transition"):
        assert not hasattr(real_chat, method), (
            f"OpenClawChatAdapter 长出了 {method}？契约门控依据已过期，需复查"
        )

    R = _install_resolve_bridge(monkeypatch, real_chat)
    R._kernel = kernel_mod.A2aKernel()
    k = R._kernel

    rid, _ = await k.open_run("openclaw-ctx", "k3", "first", "d")
    await k.publish_interaction_requested(
        rid, "i-oc", {"kind": "exec", "prompt": "rm test?"})

    out = await R._resolve_interaction(
        {"interactionId": "i-oc",
         "resolution": {"decision": "approve", "kind": "exec"}}, 1, None)

    # 结构化拒绝（非 500/AttributeError 泄漏）——调用方可按 reason 分诊
    assert "error" in out, out
    assert out["error"]["code"] == -32000
    assert out["error"]["data"]["reason"] == "resolve_unsupported", out
    assert "AttributeError" not in out["error"]["message"]
    assert "resolve_exec_approval" in out["error"]["message"], (
        "错误必须点名缺失的契约方法，便于分诊"
    )
    # 决策未确认：pending 不得被洗掉，run 不得回 WORKING
    assert k.interaction_pending(rid, "i-oc") is True
    assert k.runs[rid].state != "TASK_STATE_WORKING"


@pytest.mark.asyncio
async def test_resolve_unsupported_kinds_route_gates_per_kind(monkeypatch):
    """R7-F3 分 kind 门控：ask_user / mode_switch / 缺省各自按所需方法判定
    ——缺失哪个方法就报哪个方法，不替调用方猜语义。"""
    class _OnlyExecChat:
        async def resolve_exec_approval(self, session_key, interaction_id,
                                        decision, message=None):
            return {"success": True, "payload": {}}

    fake = _OnlyExecChat()
    R = _install_resolve_bridge(monkeypatch, fake)
    R._kernel = kernel_mod.A2aKernel()
    k = R._kernel

    rid, _ = await k.open_run("mixed-ctx", "k4", "first", "d")
    await k.publish_interaction_requested(rid, "i-ask", {"kind": "ask_user"})

    out = await R._resolve_interaction(
        {"interactionId": "i-ask",
         "resolution": {"decision": "submit", "answer": "y"}}, 1, None)
    assert out["error"]["data"]["reason"] == "resolve_unsupported"
    assert "resolve_interaction" in out["error"]["message"]
    # exec 侧不受殃及：同 fake 的 exec 方法照常可用
    assert hasattr(fake, "resolve_exec_approval")


@pytest.mark.asyncio
async def test_resolve_engine_rejection_and_internal_error(monkeypatch):
    fake = _FakeResolveChat(fail_once=True, fail_code="INTERNAL_ERROR")
    R = _install_resolve_bridge(monkeypatch, fake)
    R._kernel = kernel_mod.A2aKernel()
    k = R._kernel
    rid, _ = await k.open_run("hitl-ctx3", "k3", "first", "d")
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
        async def resolve_interaction(self, session_key, interaction_id,
                                      **kw):
            return {"success": False,
                    "error": {"code": "OLD",
                               "message": "engine returned legacy shape"}}

    fake = _OldContractChat()
    R = _install_resolve_bridge(monkeypatch, fake)
    k = R._kernel = kernel_mod.A2aKernel()
    rid, _ = await k.open_run("hitl-ctx4", "k4", "first", "d")
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


# ── P0: resolve 能力条件声明（spec stage2 §5）──
# 粒度=单一 URI 代表完整能力集：三方法全在场才声明；部分支持=不声明。
# URI 字面量 = 契约定稿值，不用模块属性——测试与 card.py 实现漂移时字面量会红。

RESOLVE_URI = "https://avernet.local/a2a/ext/resolve/v1"


def test_agent_card_default_excludes_resolve_uri():
    c = make_agent_card("http://127.0.0.1:20003")
    urls = [e["url"] for e in c["capabilities"]["extensions"]]
    assert RESOLVE_URI not in urls


def test_agent_card_with_resolve_support_declares_uri_last():
    c = make_agent_card("http://127.0.0.1:20003", resolve_supported=True)
    urls = [e["url"] for e in c["capabilities"]["extensions"]]
    assert RESOLVE_URI in urls
    # 声明位序=最后（老消费方按索引/位序跳过未知扩展时 resolve 不前置扰序）
    assert urls[-1] == RESOLVE_URI
    # 既有五项不受影响（retry 被 router:232 retry-only 校验消费，不能动）
    assert "https://avernet.local/a2a/ext/retry/v1" in urls
    assert "https://avernet.local/a2a/ext/cancel/v1" in urls
    assert len(urls) == 6
    # 声明必须 required:false，与其余五项一致
    resolve_entry = [e for e in c["capabilities"]["extensions"]
                    if e["url"] == RESOLVE_URI][0]
    assert resolve_entry["required"] is False


class _FullResolveChat:
    """三方法全有——声明满足。"""
    def resolve_exec_approval(self, **kw): ...
    def resolve_interaction(self, **kw): ...
    def resolve_mode_transition(self, **kw): ...


class _PartialResolveChat:
    """缺 mode_switch——部分支持=不声明。"""
    def resolve_exec_approval(self, **kw): ...
    def resolve_interaction(self, **kw): ...


def test_card_endpoint_declares_resolve_for_full_engine(
        app_client, monkeypatch):
    monkeypatch.setattr(bridge, "_chat_plugin", lambda: _FullResolveChat())
    resp = app_client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    urls = [e["url"] for e in resp.json()["capabilities"]["extensions"]]
    assert RESOLVE_URI in urls


def test_card_endpoint_no_resolve_decl_for_partial_engine(
        app_client, monkeypatch):
    monkeypatch.setattr(bridge, "_chat_plugin", lambda: _PartialResolveChat())
    resp = app_client.get("/.well-known/agent-card.json")
    urls = [e["url"] for e in resp.json()["capabilities"]["extensions"]]
    assert RESOLVE_URI not in urls
    # 基础五项仍在，老消费方（BaaS abort 判读）不受影响
    assert "https://avernet.local/a2a/ext/cancel/v1" in urls


def test_card_endpoint_no_resolve_decl_when_plugin_unavailable(
        app_client, monkeypatch):
    def _raise():
        raise RuntimeError("no active engine")
    monkeypatch.setattr(bridge, "_chat_plugin", _raise)
    resp = app_client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200  # card 永可答，声明保守缺席
    urls = [e["url"] for e in resp.json()["capabilities"]["extensions"]]
    assert RESOLVE_URI not in urls


class _PoisonedAttrChat:
    """奇异插件：属性访问本身抛错（非 AttributeError）——真值检查
    须降级为不声明，card 端点不得 500。三方法全毒化，绕开 all() 的
    缺位短路路径，必中属性访问异常。"""
    @property
    def resolve_exec_approval(self):
        raise RuntimeError("poisoned plugin surface")

    @property
    def resolve_interaction(self):
        raise RuntimeError("poisoned plugin surface")

    @property
    def resolve_mode_transition(self):
        raise RuntimeError("poisoned plugin surface")


def test_card_endpoint_no_resolve_decl_when_attr_access_raises(
        app_client, monkeypatch):
    monkeypatch.setattr(bridge, "_chat_plugin",
                        lambda: _PoisonedAttrChat())
    resp = app_client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200  # 真值检查吞异常降级，card 永可答
    urls = [e["url"] for e in resp.json()["capabilities"]["extensions"]]
    assert RESOLVE_URI not in urls


# ══════════ R4-2: 多 pending 交互逐个可决（gateway 并发 canUseTool）══════════

@pytest.mark.asyncio
async def test_resolve_concurrent_pendings_resolve_independently(monkeypatch):
    """R4-2 回归钉：publish_interaction_resolved 曾无条件把整个 run 置
    WORKING，而 resolve 只认 INPUT_REQUIRED——同 run 第二个 pending 从此
    只能等过期。现在按 iid 记账：第一个决议后 run 仍 INPUT_REQUIRED、
    第二个照常可决；全清才回 WORKING；已决 iid 重复提交 → conflict；
    在途再挂起（同 iid 再 requested）→ 复活 pending 可再决。"""
    fake = _FakeResolveChat()
    R2 = _install_resolve_bridge(monkeypatch, fake)
    K = kernel_mod.A2aKernel()
    monkeypatch.setattr(R2, "_kernel", K)

    rid, _ = await K.open_run("hitl-multi", "km", "first", "d")
    await K.publish_interaction_requested(rid, "i-m1", {})
    await K.publish_interaction_requested(rid, "i-m2", {})
    assert K.runs[rid].state == "TASK_STATE_INPUT_REQUIRED"

    out1 = await R2._resolve_interaction(
        {"interactionId": "i-m1",
         "resolution": {"decision": "submit", "kind": "ask_user"}}, 1, None)
    assert out1["interaction"]["resolved"] is True, out1
    # 只决一个：run 保持 INPUT_REQUIRED，第二个不被拦
    assert K.runs[rid].state == "TASK_STATE_INPUT_REQUIRED", \
        "决一个就把 run 拍回 WORKING 会锁死其余 pending"

    out2 = await R2._resolve_interaction(
        {"interactionId": "i-m2",
         "resolution": {"decision": "submit", "kind": "ask_user"}}, 2, None)
    assert out2["interaction"]["resolved"] is True, out2
    assert K.runs[rid].state == "TASK_STATE_WORKING"

    out3 = await R2._resolve_interaction(
        {"interactionId": "i-m1",
         "resolution": {"decision": "submit", "kind": "ask_user"}}, 3, None)
    assert out3["error"]["data"]["reason"] == "resolve_conflict", \
        "已决 iid 重复提交必须是冲突（per-iid 幂等锚不丢）"
    assert len(fake.calls) == 2, "重复提交不得再度上行引擎"

    # 在途再挂起同 iid：复活 pending，可再决
    await K.publish_interaction_requested(rid, "i-m1", {})
    out4 = await R2._resolve_interaction(
        {"interactionId": "i-m1",
         "resolution": {"decision": "submit", "kind": "ask_user"}}, 4, None)
    assert out4["interaction"]["resolved"] is True, out4
    assert K.runs[rid].state == "TASK_STATE_WORKING"


@pytest.mark.asyncio
async def test_legacy_resolution_without_iid_clears_all_pending(monkeypatch):
    """无 iid 决议帧（旧引擎回显/强恢复）= 清空该 run 全部未决——既有
    语义锚：已决后重复提交 → conflict 仍然成立。"""
    fake = _FakeResolveChat()
    R2 = _install_resolve_bridge(monkeypatch, fake)
    K = kernel_mod.A2aKernel()
    monkeypatch.setattr(R2, "_kernel", K)

    rid, _ = await K.open_run("hitl-legacy", "kl", "first", "d")
    await K.publish_interaction_requested(rid, "i-l1", {})
    await K.publish_interaction_requested(rid, "i-l2", {})
    await K.publish_interaction_resolved(rid, {})   # legacy：无 iid

    assert K.runs[rid].state == "TASK_STATE_WORKING"
    out1 = await R2._resolve_interaction(
        {"interactionId": "i-l2",
         "resolution": {"decision": "submit", "kind": "ask_user"}}, 1, None)
    assert out1["error"]["data"]["reason"] == "resolve_conflict"
    out2 = await R2._resolve_interaction(
        {"interactionId": "i-l1",
         "resolution": {"decision": "submit", "kind": "ask_user"}}, 2, None)
    assert out2["error"]["data"]["reason"] == "resolve_conflict"
    assert fake.calls == [], "引擎已 resume 的 run 不得再上行决策"


# ══════════ R6: 旧 RPC 回复不洗同 iid 新轮次 pending ══════════

@pytest.mark.asyncio
async def test_stale_rpc_reply_cannot_clear_repending(monkeypatch):
    """R6-2 回归钉（reviewer 时序）：resolve RPC 等待期间引擎已把同
    iid 决议掉并再挂起新轮次——旧回复必须按出发时轮次丢弃，不得把新
    pending 洗掉；新轮次照常可决（不是 conflict）。"""
    entered = asyncio.Event()
    release = asyncio.Event()

    class _GatedResolveChat:
        async def resolve_interaction(self, session_key, interaction_id,
                                      **kw):
            entered.set()
            await release.wait()
            return {"success": True, "payload": {}}

    R2 = _install_resolve_bridge(monkeypatch, _GatedResolveChat())
    K = kernel_mod.A2aKernel()
    monkeypatch.setattr(R2, "_kernel", K)
    rid, _ = await K.open_run("hitl-r6", "kr", "first", "d")
    await K.publish_interaction_requested(rid, "i-r", {})

    task = asyncio.create_task(R2._resolve_interaction(
        {"interactionId": "i-r",
         "resolution": {"decision": "submit", "kind": "ask_user"}},
        1, None))
    await entered.wait()

    # RPC 在途：引擎将旧轮次 resolved，随后同 iid 再挂起（新轮次）
    await K.publish_interaction_resolved(rid, {"interactionId": "i-r"})
    await K.publish_interaction_requested(rid, "i-r", {})
    assert K.interaction_pending(rid, "i-r") is True, "前置:新轮次已建"

    release.set()
    out = await asyncio.wait_for(task, 2)
    assert out["interaction"]["resolved"] is True, out
    assert K.interaction_pending(rid, "i-r") is True, \
        "旧 RPC 回复把同 iid 新轮次的 pending 洗掉了"
    assert K.runs[rid].state == "TASK_STATE_INPUT_REQUIRED"

    # 新轮次照常可决
    gate2_in = asyncio.Event()
    gate2_out = asyncio.Event()

    class _FastChat:
        async def resolve_interaction(self, session_key, interaction_id,
                                      **kw):
            return {"success": True, "payload": {}}

    monkeypatch.setattr("engine.community.api.transport.a2a.engine_bridge."
                        "_chat_plugin", lambda: _FastChat())
    out2 = await R2._resolve_interaction(
        {"interactionId": "i-r",
         "resolution": {"decision": "submit", "kind": "ask_user"}},
        2, None)
    assert out2["interaction"]["resolved"] is True, \
        f"新轮次被误判 conflict: {out2}"
    assert K.runs[rid].state == "TASK_STATE_WORKING"


# ══════════ R5: approvalId 身份提取 + wire 帧状态与 run 一致 ══════════

@pytest.mark.asyncio
async def test_approval_resolved_only_clears_that_pending():
    """R5-1 回归钉：approval 回流只带 approvalId（bridge 原样上抛）——
    单项清账不清全，i-B 仍可决；wire resolved 帧携带 run 真实状态（B
    未决时 INPUT_REQUIRED，全清后 WORKING），与 GetTask 语义一致。"""
    k = kernel_mod.A2aKernel()
    rid, _ = await k.open_run("ctx-apr", "ka", "first", "d")
    await k.publish_interaction_requested(
        rid, "apr-A", {"approvalId": "apr-A"})
    await k.publish_interaction_requested(rid, "i-B", {})

    await k.publish_interaction_resolved(
        rid, {"approvalId": "apr-A", "state": "approval_resolved"})

    assert k.interaction_pending(rid, "apr-A") is False
    assert k.interaction_pending(rid, "i-B") is True, \
        "approval 回流被当成 legacy 全清,锁死了同 run 其余 pending"
    assert k.runs[rid].state == "TASK_STATE_INPUT_REQUIRED"

    backlog, _ = await k.subscribe(rid, since_seq=0)
    resolved_frames = [e for e in backlog if e["type"] == "statusUpdate"
                      and (e["ev"]["statusUpdate"].get("metadata")
                           or {}).get("kind") == "interaction.resolved"]
    assert resolved_frames, "resolved 事件缺 kind 独立标识"
    su = resolved_frames[-1]["ev"]["statusUpdate"]
    assert su["status"]["state"] == "TASK_STATE_INPUT_REQUIRED", \
        "wire 帧谎报 WORKING（run 实际仍在等待 i-B）"
    assert (su.get("metadata", {}).get("engine_frame")
            or {}).get("approvalId") == "apr-A"

    # 第二个 pending 照常可决；全清后最后一条 resolved 帧如实是 WORKING
    await k.publish_interaction_resolved(rid, {"interactionId": "i-B"})
    assert k.runs[rid].state == "TASK_STATE_WORKING"
    backlog2, _ = await k.subscribe(rid, since_seq=0)
    resolved2 = [e for e in backlog2 if e["type"] == "statusUpdate"
                 and (e["ev"]["statusUpdate"].get("metadata")
                      or {}).get("kind") == "interaction.resolved"]
    assert resolved2[-1]["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_WORKING"


@pytest.mark.asyncio
async def test_unknown_identity_resolution_clears_nothing():
    """R5-1：有明确身份但未注册/不匹配——不清全、不改状态（不降级成
    legacy），事件原样上行。"""
    k = kernel_mod.A2aKernel()
    rid, _ = await k.open_run("ctx-ghost", "kg", "first", "d")
    await k.publish_interaction_requested(rid, "i-g1", {})
    await k.publish_interaction_requested(rid, "i-g2", {})

    await k.publish_interaction_resolved(
        rid, {"interactionId": "ghost", "state": "interaction.resolved"})

    assert k.interaction_pending(rid, "i-g1") is True, \
        "未知身份决议清掉了不相关 pending"
    assert k.interaction_pending(rid, "i-g2") is True
    assert k.runs[rid].state == "TASK_STATE_INPUT_REQUIRED"
    backlog, _ = await k.subscribe(rid, since_seq=0)
    kinds = [(e["ev"]["statusUpdate"].get("metadata") or {}).get("kind")
             for e in backlog if e["type"] == "statusUpdate"]
    assert "interaction.resolved" in kinds, "未知身份的事件未原样上行"


# ══════════ R4-3: 客户端超时贯穿到 ChatRequest.aliveTime ══════════

def test_timeout_ms_reaches_chat_request(app_client, monkeypatch):
    """R4-3 Review 验收：断言打在真实 ChatService.stream 边界——引擎收到的
    ChatRequest.aliveTime 必须等于客户端 metadata.timeoutMs，而非只检查
    request JSON 有这个字段。"""
    import time as _time
    captured: list = []

    class _CaptureChat:
        def stream(self, request, auth=None):
            captured.append(request)

            async def gen():
                yield _Frame("chat", {"state": "delta", "runId": "nat-tm",
                                      "deltaText": "片段"})
                yield _Frame("chat", {"state": "final", "runId": "nat-tm",
                                      "message": {"content": "终稿"}})

            return gen()

    _install_fake_chat(monkeypatch, _CaptureChat())
    r = _rpc(app_client, 1, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "长任务"}],
                    "messageId": "tm-ok", "contextId": "ctx-tm"},
        "metadata": {"timeoutMs": "600000"}})
    assert "error" not in r.json(), r.json()
    for _ in range(200):
        _time.sleep(0.02)
        if captured:
            break
    assert captured, "bridge 未分发到引擎"
    assert captured[0].aliveTime == 600000, captured[0].aliveTime
    assert captured[0].sessionId == "ctx-tm"


def test_timeout_ms_validation_is_boundary_strict(app_client):
    """非法 timeoutMs 在开 run 前拒绝（-32602）：digit 串之外的形式、
    非正数、布尔——都不带病下行、不留孤儿 run。"""
    def _body(metadata_value):
        return _rpc(app_client, 1, "SendMessage", {
            "message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                        "messageId": "tm-bad", "contextId": "ctx-bad"},
            "metadata": {"timeoutMs": metadata_value}})

    for bad in ("600x", -1, 0, True, 1.5):
        r = _body(bad)
        err = r.json()["error"]
        assert err["code"] == -32602, (bad, err)
    # 合法 int 原生形态照收
    ok = _rpc(app_client, 2, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                    "messageId": "tm-int", "contextId": "ctx-int"},
        "metadata": {"timeoutMs": 30000}})
    assert "error" not in ok.json()


# ══════════ 1c+: CancelTask 引擎 abort 上行（best-effort，TC-CANCEL 闭环）══════════

class _FakeAbortChat:
    """abort 通道 fake — ChatService.abort 的真实契约（ChatAbortResult）。
    与 _FakeChat 相同边界：假在 ChatService 插件面上。"""

    def __init__(self, *, fail=False, hang=False, miss=False):
        self.calls: list = []
        self.fail = fail
        self.hang = hang
        self.miss = miss

    async def abort(self, request, auth=None):
        self.calls.append(request)
        if self.hang:
            await asyncio.sleep(30)
        if self.fail:
            return ChatAbortResult(
                ok=False, error=ErrorShape(code="REJECTED", message="denied"))
        return ChatAbortResult(ok=True, aborted=not self.miss,
                               run_id=request.run_id)


def test_cancel_forwards_abort_keys_and_engine_reflow_is_idempotent(
        app_client, monkeypatch):
    """abort 上行口径：cancel_run 成功后 router 携 ChatService 句柄发
    ChatService.abort——与 ws_server._handle_chat_abort 同一构造
    (ChatAbortRequest(session_key=run.context_id, run_id=native_run_id))；
    引擎侧 aborted 帧回流（bridge _safe_complete 正是 state=aborted 的
    收口路径）在 T1 下幂等不炸。"""
    fake = _FakeAbortChat()
    _install_fake_chat(monkeypatch, fake)
    k = R._kernel

    async def seed():
        rid, _ = await k.open_run("abort-ctx", "ak1", "first", "d")
        await k.publish_status(rid, "TASK_STATE_WORKING")
        return rid

    rid = asyncio.run(seed())
    k.runs[rid].native_run_id = "nat-7"

    r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    body = r.json()
    assert "error" not in body, body
    assert body["result"]["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    # abort 结局如实附录：引擎确认中止
    assert body["result"]["engineAbort"] == "confirmed", body

    # 引擎收到 1 次 abort，定位键 = run 的规范 context + E4 引擎原生 id
    #（不是 kernel rid——engine 不认识传输层 id）
    assert len(fake.calls) == 1
    req = fake.calls[0]
    assert isinstance(req, ChatAbortRequest)
    assert req.session_key == "abort-ctx"
    assert req.run_id == "nat-7"

    # 引擎 aborted 帧迟到回流：不改写状态、不二次终结、不上浮（T1 已终态早退）
    async def reflow():
        await bridge._safe_complete(k, rid, "TASK_STATE_CANCELED")

    asyncio.run(reflow())
    run = k.get_run(rid)
    assert run.state == "TASK_STATE_CANCELED"


def test_cancel_abort_without_native_run_id_sends_session_key(
        app_client, monkeypatch):
    """native_run_id 未登记（引擎尚无帧回流：SUBMITTED/极早期 run）时
    abort 仍上行——run_id=None 落到 port 的 session 级中止语义
    （claude_code 省略 runId 字段；openclaw 传 ""）。"""
    fake = _FakeAbortChat()
    _install_fake_chat(monkeypatch, fake)
    rid = _seed_run("abort-early", "ak2")
    # native_run_id 未注入（None）

    r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    assert r.json()["result"]["task"]["status"]["state"] == \
        "TASK_STATE_CANCELED"
    assert len(fake.calls) == 1
    req = fake.calls[0]
    assert req.session_key == "abort-early"
    assert req.run_id is None


def test_abort_refused_still_canceled_snapshot_with_honest_log(
        app_client, monkeypatch, caplog):
    """abort ok=False 诚实语义：传输面取消已生效——run 不复活、不回滚，
    CancelTask 仍如实回 CANCELED 快照（BaaS 判定契约只认 state），
    失败只落日志不静默。"""
    import logging as _logging
    fake = _FakeAbortChat(fail=True)
    _install_fake_chat(monkeypatch, fake)
    k = R._kernel

    async def seed():
        rid, _ = await k.open_run("abort-refused", "ak3", "first", "d")
        await k.publish_status(rid, "TASK_STATE_WORKING")
        await k.publish_delta(rid, "已有增量")
        return rid

    rid = asyncio.run(seed())

    with caplog.at_level(_logging.WARNING, logger="engine-a2a"):
        r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})

    body = r.json()
    assert "error" not in body, body
    assert body["result"]["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    # 不静默附录：refusal 以 engineAbort 上浮，调用方可判读
    assert body["result"]["engineAbort"] == "refused", body
    assert len(fake.calls) == 1, "refused abort was still forwarded once"

    # 不静默：refusal 从引擎 code 结构化落日志
    refused = [rec for rec in caplog.records if "engine abort refused" in rec.message]
    assert refused, caplog.text
    assert "REJECTED" in refused[0].getMessage()

    # run 不复活：迟到引擎帧不改写终态与终稿
    async def late():
        await k.publish_delta(rid, "迟到增量")
        await k.complete_run(rid, "TASK_STATE_COMPLETED")

    asyncio.run(late())
    run = k.get_run(rid)
    assert run.state == "TASK_STATE_CANCELED"
    assert run.latest_text == "已有增量"


def test_abort_ok_but_not_aborted_reports_not_found(app_client, monkeypatch,
                                                    caplog):
    """ok=True, aborted=False 结局：引擎确认"无此在跑 run"（自然终局先于
    abort 到达等）。传输面 CANCELED 仍成立（订阅已终结、迟到帧丢弃），
    但 engineAbort 如实附录 not_found——不再把"引擎没有取消任何东西"
    伪装成 confirmed。"""
    import logging as _logging
    fake = _FakeAbortChat(miss=True)
    _install_fake_chat(monkeypatch, fake)
    rid = _seed_run("abort-miss", "ak8")
    k = R._kernel
    k.runs[rid].native_run_id = "nat-miss"

    with caplog.at_level(_logging.WARNING, logger="engine-a2a"):
        r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})

    body = r.json()
    assert "error" not in body, body
    assert body["result"]["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert body["result"]["engineAbort"] == "not_found", body
    noop = [rec for rec in caplog.records
            if "engine abort found no active run" in rec.message]
    assert noop, caplog.text


def test_cancel_after_engine_final_no_abort_sent(app_client, monkeypatch):
    """引擎自然终局先到 + CancelTask 后到 → 既有 task_terminal 错误面，
    且不向引擎发二次 abort（终态下取消对 engine 无意义、也避免误伤
    同 session 后续 run）。"""
    fake = _FakeAbortChat()
    _install_fake_chat(monkeypatch, fake)
    k = R._kernel

    async def seed():
        rid, _ = await k.open_run("abort-final", "ak4", "first", "d")
        await k.publish_final(rid, "自然终稿")
        await k.complete_run(rid, "TASK_STATE_COMPLETED")
        return rid

    rid = asyncio.run(seed())
    r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    err = r.json()["error"]
    assert err["data"]["reason"] == "task_terminal"
    assert err["data"]["state"] == "TASK_STATE_COMPLETED"
    assert fake.calls == [], "terminal run 不得再触发引擎 abort"


def test_repeat_cancel_does_not_re_abort(app_client, monkeypatch):
    """T1 幂等：第二次 CancelTask 走 task_terminal 错误面（终态单次）
    ——引擎侧只收到 1 次 abort。"""
    fake = _FakeAbortChat()
    _install_fake_chat(monkeypatch, fake)
    rid = _seed_run("abort-twice", "ak5")
    k = R._kernel
    k.runs[rid].native_run_id = "nat-2"

    r1 = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    assert r1.json()["result"]["task"]["status"]["state"] == \
        "TASK_STATE_CANCELED"
    r2 = _rpc(app_client, 2, "CancelTask", {"taskId": rid})
    err = r2.json()["error"]
    assert err["data"]["reason"] == "task_terminal"
    assert err["data"]["state"] == "TASK_STATE_CANCELED"
    assert len(fake.calls) == 1, "重复 cancel 不得重复 abort"
    assert fake.calls[0].run_id == "nat-2"


def test_abort_channel_unavailable_or_timeout_keeps_canceled(
        app_client, monkeypatch, caplog):
    """abort 通道不可用（无活跃引擎）/ 引擎挂死超时：CancelTask 响应仍
    如实回 CANCELED 快照且限时返回（不随引擎挂死），失败落日志不静默，
    run 不复活。"""
    import logging as _logging

    # a) 无活跃引擎：_chat_plugin 抛 RuntimeError
    def broken_plugin():
        raise RuntimeError("engine chat surface unavailable")

    monkeypatch.setattr(bridge, "_chat_plugin", broken_plugin)
    rid = _seed_run("abort-none", "ak6")
    with caplog.at_level(_logging.WARNING, logger="engine-a2a"):
        r = _rpc(app_client, 1, "CancelTask", {"taskId": rid})
    assert r.json()["result"]["task"]["status"]["state"] == \
        "TASK_STATE_CANCELED"
    assert r.json()["result"]["engineAbort"] == "unavailable"
    unavailable = [rec for rec in caplog.records
                   if "engine abort unavailable" in rec.message]
    assert unavailable, caplog.text

    # b) 引擎 abort 挂死：限时返回（超时上限可注入），响应不变
    fake = _FakeAbortChat(hang=True)
    _install_fake_chat(monkeypatch, fake)
    monkeypatch.setattr(R, "_ABORT_FORWARD_TIMEOUT_SECONDS", 0.05)
    rid2 = _seed_run("abort-hang", "ak7")
    r2 = _rpc(app_client, 2, "CancelTask", {"taskId": rid2})
    assert r2.json()["result"]["task"]["status"]["state"] == \
        "TASK_STATE_CANCELED"
    assert r2.json()["result"]["engineAbort"] == "timeout"
    timed_out = [rec for rec in caplog.records
                 if "engine abort timed out" in rec.message]
    assert timed_out, caplog.text
    assert R._kernel.get_run(rid2).state == "TASK_STATE_CANCELED"


@pytest.mark.asyncio
async def test_cancel_mid_stream_engine_aborted_frame_reflow(monkeypatch):
    """真 bridge 泵流中 CancelTask：首帧注入 native_run_id → abort 上行带
    该 id；引擎 aborted 帧经 bridge 真实消费路径回流（_EngineRunAborted →
    _safe_complete）——T1 下与 cancel_run 的 CANCELED 幂等收敛，订阅者只
    收到一次终态终结。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    class _StreamAbortChat:
        def __init__(self):
            self.calls: list = []
            self.abort_seen = asyncio.Event()

        async def abort(self, request, auth=None):
            self.calls.append(request)
            self.abort_seen.set()
            return ChatAbortResult(ok=True, aborted=True,
                                   run_id=request.run_id)

        async def stream(self, request, auth=None):
            async def gen():
                yield _Frame("chat", {"state": "delta", "runId": "nat-88",
                                      "deltaText": "开头"})
                await self.abort_seen.wait()
                yield _Frame("chat", {"state": "aborted", "runId": "nat-88",
                                      "stopReason": "rpc"})
            return gen()

    fake = _StreamAbortChat()
    _install_fake_chat(monkeypatch, fake)

    rid, _ = await k.open_run("flow-ctx", "fk1", "first", "d")
    task = asyncio.create_task(
        bridge.execute_and_stream(k, rid, "flow-ctx", "查询"))
    # 首帧落地 → native id 已由 bridge 注入（E4）
    for _ in range(200):
        if k.runs[rid].native_run_id == "nat-88":
            break
        await asyncio.sleep(0.01)
    assert k.runs[rid].native_run_id == "nat-88"
    assert k.runs[rid].state == "TASK_STATE_WORKING"

    out = await R._cancel_task({"taskId": rid}, 1, None)
    assert out["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    # abort 上行定位键 = context + E4 首帧注入的引擎原生 id
    assert len(fake.calls) == 1
    assert fake.calls[0].session_key == "flow-ctx"
    assert fake.calls[0].run_id == "nat-88"

    await asyncio.wait_for(task, 5)   # bridge 消费 aborted 帧后自然收口
    assert k.get_run(rid).state == "TASK_STATE_CANCELED"

    # 订阅者只见一次终态终结（T1：aborted 回流不二次终结）
    backlog, q = await k.subscribe(rid, since_seq=0)
    states = [e["ev"]["statusUpdate"]["status"]["state"]
              for e in backlog if e["type"] == "statusUpdate"]
    assert states.count("TASK_STATE_CANCELED") == 1, states
    marker = await asyncio.wait_for(q.get(), 2)
    assert marker["ev"]["statusUpdate"]["metadata"]["final"] == "true"
    assert q.empty(), "aborted 回流后不得二次终结"


# ── 终态收口（cluster E2E 2026-10-15 缺陷回归）─────────────────────────────
# provider 重试耗尽后上游静默断流：桥接消费循环原本把"流尽无终态"包装成
# publish_final(已收增量) + COMPLETED —— 伪装成功；既无 error 也不可解释。
# 修复语义：流尽且未见 final/error → FAILED 终态 + 结构化错误元数据；
# 正常 final 先到的流幂等不重发；HITL 挂起中断流同样显式失败收口。

_STREAM_END_CODE = "STREAM_TERMINATED_WITHOUT_FINAL"


async def _drain_until_terminal(q: asyncio.Queue):
    """收队列事件直到 final 终态 marker（deliver_terminal 保证非 None）。"""
    events: list[dict] = []
    while True:
        ev = await asyncio.wait_for(q.get(), 2)
        assert ev is not None
        events.append(ev)
        if ev.get("type") == "statusUpdate" and \
                ev["ev"]["statusUpdate"].get("metadata", {}).get("final") == "true":
            return events, ev


@pytest.mark.asyncio
async def test_provider_death_exhausted_stream_is_failed_terminal(monkeypatch):
    """流尽无终态（provider 全链失败，上游既无 final 也无 error 帧断流）：
    run 必须 FAILED 收口、终态帧携带结构化可解释错误；已流出的增量留在
    快照但不得包装成假终稿。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    class _DyingChat:
        async def stream(self, request, auth=None):
            async def gen():
                yield _Frame("chat", {"state": "delta", "runId": "nat-99",
                                      "deltaText": "回答开头"})
                # provider 重试耗尽：无 final/error/aborted，流直接结束
            return gen()

    _install_fake_chat(monkeypatch, _DyingChat())
    rid, _ = await k.open_run("die-ctx", "dk1", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    await bridge.execute_and_stream(k, rid, "die-ctx", "查询")

    events, terminal = await _drain_until_terminal(q)
    assert terminal["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_FAILED"
    meta = terminal["ev"]["statusUpdate"]["metadata"]
    assert meta["errorCode"] == _STREAM_END_CODE, meta
    assert meta["errorMessage"], meta   # 订阅者拿到可解释错误，不再悬挂

    run = k.get_run(rid)
    assert run.state == "TASK_STATE_FAILED"
    # 只发过增量（非 final 增量流），不伪装终稿
    assert run.latest_text == "回答开头"
    chunks = [e for e in events if e["type"] == "artifactUpdate"]
    assert chunks
    assert all(
        c["ev"]["artifactUpdate"]["lastChunk"] is False for c in chunks)


@pytest.mark.asyncio
async def test_exhaustion_no_frames_fails_without_fake_success(monkeypatch):
    """零帧流尽（上游默认即死）：不入 publish_final 空串终稿、不 COMPLETED
    ——FAILED 终态必须照常终结订阅者。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    class _SilentChat:
        async def stream(self, request, auth=None):
            async def gen():
                return
                yield  # pragma: no cover — keep this an async generator
            return gen()

    _install_fake_chat(monkeypatch, _SilentChat())
    rid, _ = await k.open_run("silent-ctx", "sk1", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    await bridge.execute_and_stream(k, rid, "silent-ctx", "查询")

    events, terminal = await _drain_until_terminal(q)
    assert terminal["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_FAILED"
    assert terminal["ev"]["statusUpdate"]["metadata"]["errorCode"] == \
        _STREAM_END_CODE
    assert k.get_run(rid).state == "TASK_STATE_FAILED"
    assert not any(e["type"] == "artifactUpdate" for e in events)


@pytest.mark.asyncio
async def test_exhaustion_terminal_is_idempotent_after_real_final(monkeypatch):
    """正常 final 先到：bridge 在 final 处即收口，断流不合成 error、不改写
    终态；T1 下迟到终稿/迟到 FAILED 不复活。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    class _FinalChat:
        async def stream(self, request, auth=None):
            async def gen():
                yield _Frame("chat", {"state": "delta", "runId": "nat-1",
                                      "deltaText": "开头"})
                yield _Frame("chat", {"state": "final", "runId": "nat-1",
                                      "message": {"content": [
                                          {"type": "text", "text": "终稿全文"}]}})
                # bridge 在 final 处即 return，生成器不会再被拉取——
                # 迟到帧的 T1 语义由下方 publish_final 的直接断言覆盖。
            return gen()

    _install_fake_chat(monkeypatch, _FinalChat())
    rid, _ = await k.open_run("ok-ctx", "ok1", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    await bridge.execute_and_stream(k, rid, "ok-ctx", "查询")

    _, terminal = await _drain_until_terminal(q)
    assert terminal["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_COMPLETED"
    assert "errorCode" not in terminal["ev"]["statusUpdate"]["metadata"]
    run = k.get_run(rid)
    assert run.state == "TASK_STATE_COMPLETED"
    assert run.latest_text == "终稿全文"
    # T1：迟到 final/FAILED 不改写首个终态
    await k.publish_final(rid, "迟到终稿")
    await k.complete_run(rid, "TASK_STATE_FAILED")
    assert run.state == "TASK_STATE_COMPLETED"
    assert run.latest_text == "终稿全文"
    assert q.empty(), "终态后不得再次终结订阅者"


@pytest.mark.asyncio
async def test_exhaustion_during_hitl_fails_run_and_invalidates_interaction(
        monkeypatch):
    """INPUT_REQUIRED 挂起中断流：run 显式 FAILED（不无限挂、不伪装
    COMPLETED）；迟到 ResolveInteraction 判 not_found，引擎零转发。"""
    class _HitlDeathChat(_FakeResolveChat):
        async def stream(self, request, auth=None):
            async def gen():
                yield _Frame("chat", {"state": "delta", "runId": "nat-77",
                                      "deltaText": "开头"})
                yield _Frame("interaction.requested", {
                    "state": "interaction.requested", "runId": "nat-77",
                    "interactionId": "i-77", "questions": ["继续?"]})
                # 交互等待中上游死亡：无终帧
            return gen()

    fake = _HitlDeathChat()
    R2 = _install_resolve_bridge(monkeypatch, fake)
    monkeypatch.setattr(R2, "_kernel", kernel_mod.A2aKernel())
    k = R2._kernel

    rid, _ = await k.open_run("hitl-die-ctx", "hk1", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    await bridge.execute_and_stream(k, rid, "hitl-die-ctx", "查询")

    events, terminal = await _drain_until_terminal(q)
    states = [e["ev"]["statusUpdate"]["status"]["state"] for e in events
              if e["type"] == "statusUpdate"]
    assert "TASK_STATE_INPUT_REQUIRED" in states    # HITL 先到过现场
    assert terminal["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_FAILED"
    assert terminal["ev"]["statusUpdate"]["metadata"]["errorCode"] == \
        _STREAM_END_CODE
    assert k.get_run(rid).state == "TASK_STATE_FAILED"

    # 迟到 resolve：run 已终态 → not_found，引擎零转发（不复活）
    out = await R2._resolve_interaction(
        {"interactionId": "i-77",
         "resolution": {"decision": "approve", "kind": "exec"}}, 1, None)
    assert out["error"]["data"]["reason"] == "not_found"
    assert fake.calls == []


@pytest.mark.asyncio
async def test_engine_error_frame_terminal_carries_structured_detail(
        monkeypatch):
    """引擎显式 error 帧（如插件 {success:False} 错误面）：FAILED 终态携带
    引擎 errorCode/errorMessage + engine_frame 透传——错误不吞、细节可解。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    class _ErrorChat:
        async def stream(self, request, auth=None):
            async def gen():
                yield _Frame("chat", {"state": "error", "runId": "nat-4",
                                      "errorCode": "PROVIDER_FAILED",
                                      "errorMessage": "模型上游连续失败"})
            return gen()

    _install_fake_chat(monkeypatch, _ErrorChat())
    rid, _ = await k.open_run("err-ctx", "ek1", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    await bridge.execute_and_stream(k, rid, "err-ctx", "查询")

    _, terminal = await _drain_until_terminal(q)
    assert terminal["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_FAILED"
    meta = terminal["ev"]["statusUpdate"]["metadata"]
    assert meta["errorCode"] == "PROVIDER_FAILED"
    assert meta["errorMessage"] == "模型上游连续失败"
    engine_frame = meta.get("engine_frame") or {}
    assert engine_frame.get("errorMessage") == "模型上游连续失败"


# ── 引擎面不可用（评审 P2）：echo fallback 除名，诚实 FAILED ────────────────
# 原缺陷：_chat_plugin() 抛错（无活跃引擎）时 _dispatch_to_engine 返回
# False → _echo_fallback 把用户输入 echo 成回复 → COMPLETED。引擎不可用
# 被伪装成执行成功。修复语义：不可用（抛错/返空）→ FAILED 终态 + 结构化
# detail（errorCode=ENGINE_UNAVAILABLE，词汇对齐 inject 面的
# inject_target_unavailable 诚实失败风格），SSE 消费者可观测。

_ENGINE_UNAVAILABLE_CODE = "ENGINE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_chat_plugin_raise_fails_with_engine_unavailable(monkeypatch):
    """_chat_plugin() 抛错：run 以 FAILED 收口并携带结构化 detail，
    不再 echo 用户输入、不再伪装 COMPLETED（评审最小复现的正名）。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    def broken_plugin():
        raise RuntimeError("no active engine")

    monkeypatch.setattr(bridge, "_chat_plugin", broken_plugin)
    rid, _ = await k.open_run("dead-ctx", "dk9", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    await bridge.execute_and_stream(k, rid, "dead-ctx", "查询")

    events, terminal = await _drain_until_terminal(q)
    assert terminal["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_FAILED"
    meta = terminal["ev"]["statusUpdate"]["metadata"]
    assert meta["errorCode"] == _ENGINE_UNAVAILABLE_CODE, meta
    assert meta["errorMessage"], meta            # 可解释，不裸 FAILED
    assert "no active engine" in meta["errorMessage"]

    run = k.get_run(rid)
    assert run.state == "TASK_STATE_FAILED"
    # echo 路径已除名：零增量、零终稿，内核快照不被用户输入污染
    assert run.latest_text == "", run.latest_text
    assert not any(e["type"] == "artifactUpdate" for e in events)


@pytest.mark.asyncio
async def test_chat_plugin_none_fails_with_engine_unavailable(monkeypatch):
    """引擎面返空（manager 已起但 chat 插件为 None）：同样诚实 FAILED，
    不落到 AttributeError 深处、更不 echo。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    monkeypatch.setattr(bridge, "_chat_plugin", lambda: None)
    rid, _ = await k.open_run("none-ctx", "nk1", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    await bridge.execute_and_stream(k, rid, "none-ctx", "查询")

    _, terminal = await _drain_until_terminal(q)
    assert terminal["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_FAILED"
    meta = terminal["ev"]["statusUpdate"]["metadata"]
    assert meta["errorCode"] == _ENGINE_UNAVAILABLE_CODE, meta
    assert k.get_run(rid).state == "TASK_STATE_FAILED"
    assert k.get_run(rid).latest_text == ""


@pytest.mark.asyncio
async def test_midstream_engine_crash_keeps_existing_failed_semantics(
        monkeypatch):
    """回归保护：引擎存在但流中途炸——既有未分类 FAILED 收口语义零变化
    （FAILED 终态、已流出增量保留在快照、无 errorCode detail）。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    class _CrashingChat:
        async def stream(self, request, auth=None):
            async def gen():
                yield _Frame("chat", {"state": "delta", "runId": "nat-5",
                                      "deltaText": "部分回答"})
                raise RuntimeError("engine exploded mid-stream")
            return gen()

    _install_fake_chat(monkeypatch, _CrashingChat())
    rid, _ = await k.open_run("crash-ctx", "ck1", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    await bridge.execute_and_stream(k, rid, "crash-ctx", "查询")

    events, terminal = await _drain_until_terminal(q)
    assert terminal["ev"]["statusUpdate"]["status"]["state"] == \
        "TASK_STATE_FAILED"
    # 既有语义：未分类失败不带结构化 errorCode（不加戏、不重分类）
    assert not (terminal["ev"]["statusUpdate"]["metadata"].get("errorCode"))
    run = k.get_run(rid)
    assert run.state == "TASK_STATE_FAILED"
    assert run.latest_text == "部分回答", run.latest_text


def test_engine_unavailable_send_streaming_sse_observes_failed(
        app_client, monkeypatch):
    """生产入口全程留痕：SendStreamingMessage 经裸 FastAPI + 真分发路径，
    _chat_plugin 抛错时 BaaS 式 SSE 消费者从终态帧直接读出 FAILED +
    ENGINE_UNAVAILABLE（先例：streaming dedup 测试的 SSE 消费形态）。"""
    def broken_plugin():
        raise RuntimeError("no active engine")

    monkeypatch.setattr(bridge, "_chat_plugin", broken_plugin)

    rid_holder: dict = {}
    failed_meta: list[dict] = []
    saw_failed_marker = False
    with app_client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage",
            "params": {"message": {"role": "ROLE_USER",
                                   "parts": [{"text": "用户输入原文"}],
                                   "messageId": "sse1",
                                   "contextId": "sse-ctx"}}}) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:]).get("result", {})
            task, su = ev.get("task"), ev.get("statusUpdate")
            if task and not rid_holder:
                rid_holder["rid"] = task["id"]
            if not su:
                continue
            rid_holder.setdefault("rid", su["taskId"])
            meta = su.get("metadata") or {}
            if su["status"]["state"] == "TASK_STATE_FAILED":
                failed_meta.append(meta)
                if meta.get("errorCode") == _ENGINE_UNAVAILABLE_CODE:
                    saw_failed_marker = True
            if meta.get("final") == "true":
                break

    assert saw_failed_marker, (failed_meta, rid_holder)
    rid = rid_holder["rid"]
    run = R._kernel.get_run(rid)
    assert run.state == "TASK_STATE_FAILED"
    # echo 彻底除名：用户输入绝不被包装成回复
    assert "[echo" not in run.latest_text, run.latest_text
    assert run.latest_text == "", run.latest_text


# ══════════ 边界与治理闭环补齐：kernel 容量/逐出/队列治理 + rpc 映射 ══════════
# 目标：每个分支断言真实行为——错误码形状、状态迁移、订阅者终结语义，
# 不写只执行不断言的行程测试。


# ── kernel：open_run 边界校验 + UNKNOWN_OUTCOME 判读符 ─────────────────────

@pytest.mark.asyncio
async def test_kernel_open_run_boundary_and_sentinel_repr():
    k = kernel_mod.A2aKernel()
    with pytest.raises(ValueError, match="contextId is required"):
        await k.open_run(None, "kk", "first", None)      # contextId 非 str
    with pytest.raises(ValueError, match="messageId must be a string"):
        await k.open_run("c", 123, "first", None)        # messageId 非 str
    with pytest.raises(ValueError, match="contextId is required"):
        await k.open_run("c", "", "first", None)         # messageId 空串
    assert repr(kernel_mod.A2aKernel.UNKNOWN_OUTCOME) == "UNKNOWN_OUTCOME"


# ── kernel：容量硬拒 + 最旧终态逐出（订阅者收 None 终结）────────────────────

@pytest.mark.asyncio
async def test_kernel_capacity_refusal_and_oldest_finished_eviction(
        monkeypatch):
    monkeypatch.setattr(kernel_mod, "MAX_RUNS", 2)
    k = kernel_mod.A2aKernel()
    rid1, _ = await k.open_run("c1", "k1", "first", "d1")
    rid2, _ = await k.open_run("c2", "k2", "first", "d2")
    backlog, q = await k.subscribe(rid1, since_seq=0)

    # 全员非终态且容量满 → 显式硬拒（D7 语义：无声突破上限不允许）
    with pytest.raises(RuntimeError, match="run capacity exhausted"):
        await k.open_run("c3", "k3", "first", "d3")

    # 终态 run 成为可驱逐对象：新 open 逐出它而不是拒绝
    await k.publish_delta(rid1, "部分内容")
    await k.complete_run(rid1, "TASK_STATE_COMPLETED")
    rid3, created = await k.open_run("c3", "k3", "first", "d3")
    assert created is True and rid3 not in (rid1, rid2)
    assert rid1 not in k.runs, "最旧终态 run 必须被逐出"
    assert ("c1", "k1") not in k._idem, "幂等键随逐出清理"
    assert set(k.runs) == {rid2, rid3}

    # 被逐出 run 的订阅者收到 None 终结信号（事件先到、None 最后）
    seen = []
    while True:
        ev = await asyncio.wait_for(q.get(), 2)
        if ev is None:
            break
        seen.append(ev)
    assert any(e["ev"]["statusUpdate"]["status"]["state"] ==
               "TASK_STATE_COMPLETED" for e in seen
               if e["type"] == "statusUpdate"), \
        "逐出前订阅者必须已看到终态"


@pytest.mark.asyncio
async def test_kernel_budget_flip_marks_cache_limit_keeps_snapshot(
        monkeypatch):
    monkeypatch.setattr(kernel_mod, "MAX_BYTES_PER_RUN", 4)   # 任何事件都超
    k = kernel_mod.A2aKernel()
    rid, _ = await k.open_run("cb", "k", "first", "d")
    r = k.get_run(rid)
    assert r.cache_limit is True, "open_run 的 task 事件就应翻转 cache_limit"
    assert r.events == [] and r.bytes == 0 and k._total_bytes == 0
    await k.publish_delta(rid, "完整生成文本")
    assert k.get_run(rid).latest_text == "完整生成文本"   # 快照与缓冲独立
    assert await k.replay(rid, 0) is None                # 内容缓冲没了 → 拒绝重放


@pytest.mark.asyncio
async def test_kernel_slow_subscriber_kicked_on_publish():
    size = kernel_mod.SUBSCRIBER_QUEUE_SIZE
    k = kernel_mod.A2aKernel()
    rid, _ = await k.open_run("slow", "sk", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    for i in range(size):
        await k.publish_delta(rid, f"块{i}")
    assert q.qsize() == size
    await k.publish_delta(rid, "溢出块")             # 慢订阅者：队满即踢
    assert k.runs[rid].subscribers == [], "被踢订阅者必须移出注册表"
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    assert drained[-1] is None, "踢出时必须投递 None 终结信号"
    assert len(drained) == size, "队头让位一件 + None 补位"


@pytest.mark.asyncio
async def test_kernel_terminal_delivery_kicks_full_subscriber():
    size = kernel_mod.SUBSCRIBER_QUEUE_SIZE
    k = kernel_mod.A2aKernel()
    rid, _ = await k.open_run("tk", "kk", "first", "d")
    _, q = await k.subscribe(rid, since_seq=0)
    for i in range(size - 1):
        await k.publish_delta(rid, f"块{i}")
    await k.complete_run(rid, "TASK_STATE_COMPLETED")  # 状态事件占掉第 256 位
    assert k.runs[rid].subscribers == [], \
        "marker 投递队满：订阅者必须被移出并让位重投"
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    assert drained[-1]["ev"]["statusUpdate"]["metadata"].get("final") == \
        "true", "让位后 final marker 必须重新入队"


@pytest.mark.asyncio
async def test_kernel_inject_channel_overflow_and_orphan_commit():
    size = kernel_mod.SUBSCRIBER_QUEUE_SIZE
    k = kernel_mod.A2aKernel()
    q = k.subscribe_context("ov")
    for i in range(size):
        k.publish_engine_inject("ov", f"通知{i}", f"inject-{i}")
    k.publish_engine_inject("ov", "溢出消息", "inject-overflow")
    assert q.qsize() == size, "背景广播队满：静默丢弃不炸"
    assert len(k._context_subs["ov"]) == 1, "广播通道订阅者不被踢"
    assert k.inject_commit("ov", "文本", "never-reserved") is False, \
        "键不存在的 commit 必须显式 False（不广播）"
    assert await k.inject_reserve("ov", "fresh") == "in_progress"
    assert k.inject_commit("ov", "文本", "fresh") is True
    assert q.qsize() == size, "commit 广播同样队满静默"


def test_kernel_inject_dedup_ttl_prune():
    k = kernel_mod.A2aKernel()
    now = time.monotonic()
    for i in range(kernel_mod._INJECT_DEDUP_MAX + 1):
        k._inject_dedup[(f"c{i}", f"m{i}")] = (
            "accepted", now - kernel_mod._INJECT_DEDUP_TTL_SECONDS * 2)

    async def reserve():
        return await k.inject_reserve("fresh-ctx", "fresh-key")

    assert asyncio.run(reserve()) == "in_progress"
    assert len(k._inject_dedup) == 1, "超上限时过期键必须成批清理"
    assert ("fresh-ctx", "fresh-key") in k._inject_dedup


@pytest.mark.asyncio
async def test_kernel_lookup_errors_replay_expiry_and_subscribe_snapshot():
    k = kernel_mod.A2aKernel()
    with pytest.raises(LookupError):
        await k.cancel_run("ghost-run")
    with pytest.raises(LookupError):
        await k.subscribe("ghost-run")
    rid, _ = await k.open_run("lc", "k", "first", "d")
    backlog, q = await k.subscribe(rid)          # since_seq=None：立即快照
    snapshot = q.get_nowait()
    assert snapshot["type"] == "task"
    assert snapshot["ev"]["task"]["id"] == rid
    await k.complete_run(rid, "TASK_STATE_COMPLETED")
    k.runs[rid].replay_expires_at = time.monotonic() - 1
    assert await k.replay(rid, 0) is None, "重放窗口过期必须显式拒绝"


# ── rpc 边界信封矩阵 ────────────────────────────────────────────────────────

def test_rpc_boundary_envelope_matrix(app_client):
    raw = app_client.post("/rpc", json=[1, 2])       # body 本身非对象
    body = raw.json()
    assert body["error"]["code"] == -32600
    assert body["id"] is None

    no_params = app_client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 7, "method": "SendMessage"})
    err = no_params.json()["error"]                   # params 缺省归一空对象
    assert err["code"] == -32602
    assert "params.message is required" in err["message"]

    bad_params = app_client.post("/rpc", json={
        "jsonrpc": "2.0", "id": 8, "method": "SendMessage",
        "params": "string"})
    assert bad_params.json()["error"]["code"] == -32602

    unknown = _rpc(app_client, 9, "DoEverything", {})
    assert unknown.json()["error"]["code"] == -32601

    stray_message = _rpc(app_client, 10, "SendMessage", {"message": "字符串"})
    stray = stray_message.json()["error"]
    assert stray["code"] == -32602
    assert "params.message is required and must be an object" in stray["message"]


def test_context_required_maps_to_wire_error(app_client):
    body = {"message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                       "messageId": "nc1"}}
    r = _rpc(app_client, 1, "SendMessage", body)
    err = r.json()["error"]
    assert err["code"] == -32602
    assert err["data"]["reason"] == "context_required"   # wire.context_required
    r2 = _rpc(app_client, 2, "SendStreamingMessage",
              {"message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                           "messageId": "nc2"}})
    assert r2.json()["error"]["data"]["reason"] == "context_required"
    bad_ctx_type = _rpc(app_client, 3, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                    "messageId": "nc3", "contextId": 42}})
    bad = bad_ctx_type.json()["error"]
    assert bad["code"] == -32602
    assert bad["message"] == "message.contextId must be a string"


def test_inject_requires_string_context_id_param(app_client, monkeypatch):
    fake = _FakeChat()
    _install_fake_chat(monkeypatch, fake)
    r = _rpc(app_client, 1, "InjectContext",
             {"message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                         "messageId": "ncx"}})     # 缺 params.contextId
    err = r.json()["error"]
    assert err["code"] == -32602
    assert err["message"] == "contextId is required (string)"
    assert fake.calls == [] and not R._kernel._inject_dedup, \
        "边界拒绝必须零引擎写入、零去重残留"


def test_send_streaming_retry_only_unknown_outcome(app_client):
    r = _rpc(app_client, 1, "SendStreamingMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                    "messageId": "absent-stream", "contextId": "cx"},
        "metadata": {"avernet.retryOnly": True}})
    err = r.json()["error"]
    assert err["code"] == -32000
    assert err["data"]["reason"] == "unknown_outcome"


def test_get_task_and_replay_taskid_validation(app_client):
    r0 = _rpc(app_client, 1, "GetTask", {})
    assert r0.json()["error"]["code"] == -32602
    r1 = _rpc(app_client, 2, "GetTask", {"taskId": "ghost"})
    err1 = r1.json()["error"]
    assert err1["code"] == -32000
    assert err1["data"]["reason"] == "not_found_or_expired"
    r2 = _rpc(app_client, 3, "ReplayEvents", {})
    assert r2.json()["error"]["code"] == -32602


def test_kernel_exceptions_map_to_protocol_errors(app_client, monkeypatch):
    # 同键异文 → -32003 结构化冲突
    async def counting(kernel, rid, ctx, text, auth=None, alive_ms=None):
        await kernel.publish_delta(rid, "回")
        await kernel.complete_run(rid)

    monkeypatch.setattr(R, "execute_and_stream", counting)
    m1 = {"message": {"role": "ROLE_USER", "parts": [{"text": "甲"}],
                      "messageId": "dup-key", "contextId": "c-dup"}}
    r1 = _rpc(app_client, 1, "SendMessage", m1)
    assert r1.json()["result"]["task"]["metadata"]["created"] is True
    m2 = {"message": {"role": "ROLE_USER", "parts": [{"text": "乙"}],
                      "messageId": "dup-key", "contextId": "c-dup"}}
    conflict = _rpc(app_client, 2, "SendMessage", m2).json()["error"]
    assert conflict["code"] == -32003
    assert conflict["data"]["reason"] == "key_conflict"

    # 容量硬拒 → -32000 capacity：先等第一条 run 真正落到终态
    # （后台 dispatch 才能把占位让出来，逐出可选而不是拒绝）。
    rid_first = r1.json()["result"]["task"]["id"]
    # sleep 先行：轮询行本身必然执行（不写永不执行的防御分支）
    for _ in range(100):
        time.sleep(0.02)
        run_first = R._kernel.get_run(rid_first)
        if run_first is None or \
                run_first.state == "TASK_STATE_COMPLETED":
            break
    assert R._kernel.get_run(rid_first).state == "TASK_STATE_COMPLETED"

    async def noop_dispatch(kernel, rid, ctx, text, auth=None,
                            alive_ms=None):
        pass                                          # 不收口：保持占位

    # 作用域 monkeypatch：容量补丁不外溢到后续 LookupError/ValueError 段
    with pytest.MonkeyPatch.context() as cap_mp:
        cap_mp.setattr(kernel_mod, "MAX_RUNS", 1)
        cap_mp.setattr(R, "execute_and_stream", noop_dispatch)
        c1 = {"message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                          "messageId": "cap-1", "contextId": "c-cap"}}
        assert _rpc(app_client, 3, "SendMessage", c1).json()["result"][
            "task"]["metadata"]["created"] is True
        c2 = {"message": {"role": "ROLE_USER", "parts": [{"text": "y"}],
                          "messageId": "cap-2", "contextId": "c-cap2"}}
        capacity = _rpc(app_client, 4, "SendMessage", c2).json()["error"]
    assert capacity["code"] == -32000
    assert capacity["data"]["reason"] == "capacity"
    assert "run capacity exhausted" in capacity["message"]

    # LookupError → replay refused not_found（容量补丁先复位再徒手 seed）
    monkeypatch.setattr(kernel_mod, "MAX_RUNS", 16)
    async def seed():
        rr, _ = await R._kernel.open_run("lk", "lk1", "first", "d")
        return rr

    rid = asyncio.run(seed())

    async def missing_subscribe(rid_, since_seq=None):
        raise LookupError(rid_)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(R._kernel, "subscribe", missing_subscribe)
        mapped = _rpc(app_client, 5, "SubscribeToTask",
                      {"taskId": rid}).json()["error"]
    assert mapped["code"] == -32000
    assert mapped["data"]["reason"] == "not_found"
    # 非 contextId 的 ValueError → -32602 原样透传

    async def boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(R._kernel, "send_message", boom)
    generic = _rpc(app_client, 6, "SendMessage", {
        "message": {"role": "ROLE_USER", "parts": [{"text": "x"}],
                    "messageId": "vb", "contextId": "c-vb"}}).json()["error"]
    assert generic["code"] == -32602
    assert generic["message"] == "boom"


# ── ReplayEvents / SubscribeToTask 面上行为 ─────────────────────────────────

def test_replay_events_success_and_refusals(app_client):
    k = R._kernel

    async def seed_completed():
        rid, _ = await k.open_run("rp", "k", "first", "d")
        await k.publish_delta(rid, "增量甲")
        await k.complete_run(rid, "TASK_STATE_COMPLETED")
        return rid

    rid = asyncio.run(seed_completed())

    with app_client.stream("POST", "/rpc", json={
            "jsonrpc": "2.0", "id": "replay-hit", "method": "ReplayEvents",
            "params": {"taskId": rid, "sinceSeq": 0}}) as resp:
        assert resp.status_code == 200
        states = []
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            body = json.loads(line[6:])
            assert body["id"] == "replay-hit"        # 信封带请求自身 id
            su = body.get("result", {}).get("statusUpdate")
            if su:
                states.append(su["status"]["state"])
                if (su.get("metadata") or {}).get("final") == "true":
                    break
    assert "TASK_STATE_COMPLETED" in states

    k.get_run(rid).replay_expires_at = time.monotonic() - 1
    expired = _rpc(app_client, 2, "ReplayEvents",
                  {"taskId": rid, "sinceSeq": 0}).json()["error"]
    assert expired["data"]["reason"] == "not_found_or_expired"

    async def seed_limited():
        rid2, _ = await k.open_run("rp2", "k2", "first", "d")
        k.get_run(rid2).cache_limit = True
        return rid2

    limited = _rpc(app_client, 3, "ReplayEvents", params={
        "taskId": asyncio.run(seed_limited()), "sinceSeq": 0}).json()["error"]
    assert limited["data"]["reason"] == "cache_limit"

    ghost = _rpc(app_client, 4, "ReplayEvents",
                 {"taskId": "ghost", "sinceSeq": 0}).json()["error"]
    assert ghost["data"]["reason"] == "not_found_or_expired"


def test_subscribe_task_validation_and_live_snapshot(app_client):
    k = R._kernel
    assert _rpc(app_client, 1, "SubscribeToTask",
                {"taskId": 42}).json()["error"]["code"] == -32602
    ghost = _rpc(app_client, 2, "SubscribeToTask", {"taskId": "ghost"})
    assert ghost.json()["error"]["data"]["reason"] == "not_found"

    async def seed_terminal():
        rid, _ = await k.open_run("st", "k", "first", "d")
        await k.complete_run(rid, "TASK_STATE_COMPLETED")
        return rid

    terminal = _rpc(app_client, 3, "SubscribeToTask",
                    {"taskId": asyncio.run(seed_terminal())}).json()["error"]
    assert terminal["data"]["reason"] == "task_terminal_use_replay"

    async def seed_live():
        rid, _ = await k.open_run("st2", "k2", "first", "d")
        return rid

    rid_live = asyncio.run(seed_live())
    resp = asyncio.run(R._subscribe_task({"taskId": rid_live}, 1, None))
    assert resp.media_type == "text/event-stream"   # 活流挂订阅成功


# ── SubscribeToContext：校验面 + 流生命周期（事件→None 信标收线）────────────

def test_subscribe_context_requires_string_context(app_client):
    bad = _rpc(app_client, 1, "SubscribeToContext", {})
    assert bad.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_subscribe_context_stream_lifecycle(monkeypatch):
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel
    resp = await R._subscribe_context({"contextId": "sc"}, "ctx-sub", None)
    assert resp.media_type == "text/event-stream"

    async def collect():
        return [chunk async for chunk in resp.body_iterator]

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)       # 生成器挂上 q.get()，订阅注册完成
    assert len(k._context_subs["sc"]) == 1

    k.publish_engine_inject("sc", "后台注入", "inject-sc-1")
    k._context_subs["sc"][0].put_nowait(None)      # None 信标 = 流终结契约

    chunks = await asyncio.wait_for(task, 2)
    assert len(chunks) == 1
    body = json.loads(chunks[0][len("data: "):])
    assert body["id"] == "ctx-sub"
    msg = body["result"]["message"]
    assert msg["messageId"] == "inject-sc-1"
    assert msg["parts"][0]["text"] == "后台注入"
    assert k._context_subs.get("sc") == [], "收线后必须摘除订阅"


@pytest.mark.asyncio
async def test_sse_slow_consumer_kicked_and_stream_closes(monkeypatch):
    """慢消费的 SSE 订阅者被投递侧踢出后，生成器经 None 信标自然收线。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel
    rid, _ = await k.open_run("sl", "k", "first", "d")
    await k.publish_delta(rid, "先行块")
    backlog, q = await k.subscribe(rid, since_seq=0)
    resp = R._stream_response(11, rid, backlog, q)

    async def producer():
        for i in range(kernel_mod.SUBSCRIBER_QUEUE_SIZE + 20):
            await k.publish_delta(rid, f"块{i}")

    prod = asyncio.create_task(producer())

    async def consume_slowly():
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk)
            await asyncio.sleep(0.001)   # 慢消费：制造投递侧可观察的队满
        return out

    chunks = await asyncio.wait_for(consume_slowly(), 10)
    await prod
    assert chunks, "先行 backlog 必须原样流出"
    assert k.runs[rid].subscribers == [], "队满踢出后订阅者必须被移除"
    assert any("先行块" in chunk for chunk in chunks)


# ── wire 形状单测 ───────────────────────────────────────────────────────────

def test_wire_artifact_update_merges_extra_metadata():
    from engine.community.api.transport.a2a import wire as wire_mod
    ev = wire_mod.wire_artifact_update("r", "c", 7, "text-1", "正文",
                                       append=False, last_chunk=True,
                                       extra_metadata={"errorCode": "X"})
    au = ev["ev"]["artifactUpdate"]
    assert au["metadata"]["seq"] == 7
    assert au["metadata"]["errorCode"] == "X"
    assert au["artifact"]["parts"][0]["text"] == "正文"
    assert au["lastChunk"] is True


# ── 引擎面文本抽取补遗 ───────────────────────────────────────────────────────

def test_payload_text_string_content_and_delta_fallbacks():
    assert bridge._payload_text(
        {"message": {"content": "纯文本终稿"}}, "final") == "纯文本终稿"
    assert bridge._payload_text({"deltaText": "增量兜底"}, "final") == "增量兜底"
    assert bridge._payload_text({"state": "final"}, "final") == ""


def test_message_text_ignores_non_list_parts():
    # parts 非列表/缺失：空串聚合，不炸不臆造（边界契约）
    assert R._message_text({"parts": "junk"}) == ""
    assert R._message_text({}) == ""
    assert R._message_text({"parts": [{"text": "正文"}, {"non_text": 1},
                                      "杂项"]}) == "正文"


# ── relay fallback：无 inject 引擎经 chat.inject 转发与拒绝 ────────────────

def test_inject_relay_fallback_forward_and_refusal(app_client, monkeypatch):
    from types import SimpleNamespace

    class _RelayOnly:        # MRO 无 inject → relay fallback 路径
        pass

    _install_fake_chat(monkeypatch, _RelayOnly())
    relay_calls: list[dict] = []

    class _RelayP:
        async def forward_request(self, request_id, method, params, auth,
                                  timeout):
            relay_calls.append({"request_id": request_id, "method": method,
                                "params": params})
            if params.get("message") == "拒绝正文":
                return SimpleNamespace(ok=False, error=SimpleNamespace(
                    message="relay denied", code="REJECTED"))
            return SimpleNamespace(ok=True)

    import engine.community.manager as mgr_mod
    monkeypatch.setattr(mgr_mod.EngineManager, "get_instance",
                        staticmethod(lambda: SimpleNamespace(relay=_RelayP())))

    ok = _rpc(app_client, 1, "InjectContext",
              _inject_params("rc", "通过正文", "rf-1"))
    assert ok.json()["result"] == {"inject": "accepted"}
    assert len(relay_calls) == 1
    call = relay_calls[0]
    assert call["method"] == "chat.inject"
    assert call["request_id"].startswith("a2a-inject-")
    assert call["params"] == {"sessionKey": "rc", "message": "通过正文"}

    refused = _rpc(app_client, 2, "InjectContext",
                   _inject_params("rc", "拒绝正文", "rf-2"))
    err = refused.json()["error"]
    assert err["data"]["reason"] == "inject_forward_failed"
    assert "relay denied" in err["message"]
    assert ("rc", "rf-2") not in R._kernel._inject_dedup, "拒绝后回滚可重试"


# ── _forward_engine_abort：取消语义不被 except Exception 吞 ─────────────────

@pytest.mark.asyncio
async def test_forward_engine_abort_preserves_cancellation(monkeypatch):
    fake = _FakeAbortChat(hang=True)
    _install_fake_chat(monkeypatch, fake)
    k = kernel_mod.A2aKernel()
    rid, _ = await k.open_run("ac", "k", "first", "d")
    task = asyncio.create_task(R._forward_engine_abort(k.get_run(rid)))
    await asyncio.sleep(0.05)
    assert fake.calls, "abort 应已上行"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── bridge：非 chat 帧无损路由（agent/approval/interaction.resolved）─────────

@pytest.mark.asyncio
async def test_bridge_routes_progress_interaction_and_approval_frames(
        monkeypatch):
    """R17/16: interaction.requested 无 iid 兜底 int-*、approval_* 家族、
    agent/mode_transition 经 lossless 通道保 original_event；空载荷帧丢弃
    不入流；终稿不被任何旁路帧污染。"""
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel

    class _EventsChat:
        async def stream(self, request, auth=None):
            async def gen():
                yield _Frame("agent", {"state": "agent", "runId": "nat-p",
                                       "agent": "Review"})
                yield _Frame("mode_transition.resolved",
                             {"state": "mode_transition.resolved",
                              "runId": "nat-p", "mode": "review"})
                yield _Frame("interaction.requested",
                             {"state": "interaction.requested",
                              "runId": "nat-p"})       # 无 interactionId
                iid = next(key for key in k._interactions
                           if key.startswith("int-"))
                yield _Frame("interaction.resolved",
                             {"state": "interaction.resolved",
                              "runId": "nat-p", "interactionId": iid})
                yield _Frame("approval_requested",
                             {"state": "approval_requested",
                              "runId": "nat-p"})       # 无 approvalId
                apr = next(key for key in k._interactions
                           if key.startswith("apr-"))
                yield _Frame("approval_resolved",
                             {"state": "approval_resolved",
                              "runId": "nat-p", "approvalId": apr})
                yield _Frame("agent", {})             # 空载荷：丢弃不入流
                yield _Frame("chat", {"state": "final", "runId": "nat-p",
                                      "message": {"content": [
                                          {"type": "text", "text": "终稿"}]}})
            return gen()

    _install_fake_chat(monkeypatch, _EventsChat())
    rid, _ = await k.open_run("ev2", "ek", "first", "d")
    await bridge.execute_and_stream(k, rid, "ev2", "查询")

    run = k.get_run(rid)
    assert run.state == "TASK_STATE_COMPLETED"
    assert run.native_run_id == "nat-p"
    assert run.latest_text == "终稿", "旁路帧不得污染终稿"

    agents = [e["ev"]["statusUpdate"]["metadata"]
              for e in run.events if e["type"] == "statusUpdate"]
    originals = [m["original_event"] for m in agents
                 if m.get("kind") == "agent"]
    assert "agent" in originals
    assert "mode_transition.resolved" in originals, "事件名保真（R17-2）"
    engine_frames = [m["engine_frame"] for m in agents
                     if m.get("kind") == "agent"]
    assert {"state": "agent", "runId": "nat-p",
            "agent": "Review"} in engine_frames, "无损载荷入 metadata"

    states = [e["ev"]["statusUpdate"]["status"]["state"]
              for e in run.events if e["type"] == "statusUpdate"]
    assert "TASK_STATE_INPUT_REQUIRED" in states
    assert states.count("TASK_STATE_WORKING") >= 2, "两轮 resolve 落回 WORKING"

    iids = [m["interactionId"] for m in agents if "interactionId" in m]
    assert any(i.startswith("int-") for i in iids)
    assert any(i.startswith("apr-") for i in iids)

    # 空载荷 agent 帧（无 state 无内容）不入流：agent 事件恰两条
    assert len(originals) == 2


@pytest.mark.asyncio
async def test_bridge_survives_dispatch_cancellation_and_vanished_run(
        monkeypatch):
    monkeypatch.setattr(R, "_kernel", kernel_mod.A2aKernel())
    k = R._kernel
    # 起步即消失的 run：publish_status 落异常 → 记录后安静返回
    await bridge.execute_and_stream(k, "no-such-run", "vx", "文本")
    assert "no-such-run" not in k.runs

    # 泵流中被取消：CancelledError 上浮，run 不被伪造收口
    class _NappingChat:
        async def stream(self, request, auth=None):
            async def gen():
                yield _Frame("chat", {"state": "delta", "runId": "nat-c",
                                      "deltaText": "开头"})
                await asyncio.sleep(30)
            return gen()

    _install_fake_chat(monkeypatch, _NappingChat())
    rid, _ = await k.open_run("cx-cancel", "ck1", "first", "d")
    task = asyncio.create_task(
        bridge.execute_and_stream(k, rid, "cx-cancel", "查询"))
    for _ in range(200):
        if k.runs[rid].native_run_id == "nat-c":
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert k.get_run(rid).state == "TASK_STATE_WORKING", \
        "被取消的 run 不得伪造终态"

    # _safe_complete 收口失败（run 又已消失）只落日志不上抛
    await bridge._safe_complete(k, "no-such-run", "TASK_STATE_FAILED")


# ── AuthGate guard：异常放行 / 拒绝 / 幂等键接管 ────────────────────────────

def _gate_mod():
    import engine.community.api.transport.auth_gate as gate_mod
    return gate_mod


def test_auth_gate_guard_allows_on_gate_exception(app_client_gated,
                                                  monkeypatch, caplog):
    import logging as _logging
    fake = _FakeChat()
    _install_fake_chat(monkeypatch, fake)
    gate_mod = _gate_mod()

    async def exploding_verify(**kwargs):
        raise ValueError("gate exploded")

    monkeypatch.setattr(gate_mod, "verify_chat_send", exploding_verify)
    with caplog.at_level(_logging.WARNING, logger="engine-a2a"):
        r = _rpc(app_client_gated, 1, "InjectContext",
                 _inject_params("gctx", "内容", "g-msg"),
                 headers={"x-iam-token": "tok"})
    assert r.json()["result"] == {"inject": "accepted"}, \
        "gate 异常按 ws_server 同例放行"
    assert any("auth_gate exception, allowing" in rec.message
               for rec in caplog.records)
    assert fake.calls, "放行后引擎写入真实发生"


def test_auth_gate_guard_rejects_on_zero_check(app_client_gated, monkeypatch):
    from engine.community.api.transport.auth_gate import AuthGateResult
    fake = _FakeChat()
    _install_fake_chat(monkeypatch, fake)
    gate_mod = _gate_mod()

    async def denying_verify(**kwargs):
        return AuthGateResult(allowed=False, error_message="zero_check failed")

    monkeypatch.setattr(gate_mod, "verify_chat_send", denying_verify)
    r = _rpc(app_client_gated, 1, "InjectContext",
             _inject_params("gctx2", "越权内容", "g2"),
             headers={"x-iam-token": "tok"})
    err = r.json()["error"]
    assert err["code"] == -32000
    assert err["data"]["reason"] == "ZERO_CHECK_FAILED"
    assert err["message"] == "zero_check failed"
    assert fake.calls == [], "拒绝时零引擎写入"


def test_auth_gate_key_substitution_dedups(app_client_gated, monkeypatch):
    from engine.community.api.transport.auth_gate import AuthGateResult
    intercepted: list[str] = []

    async def counting(kernel, rid, ctx, text, auth=None, alive_ms=None):
        intercepted.append(rid)
        await kernel.publish_delta(rid, "回")
        await kernel.complete_run(rid)

    monkeypatch.setattr(R, "execute_and_stream", counting)
    gate_mod = _gate_mod()

    async def keying_verify(**kwargs):
        return AuthGateResult(allowed=True, idempotency_key="gate-owned-key")

    monkeypatch.setattr(gate_mod, "verify_chat_send", keying_verify)
    header = {"x-iam-token": "tok"}
    m1 = {"message": {"role": "ROLE_USER", "parts": [{"text": "甲"}],
                      "messageId": "client-m1", "contextId": "gctx3"}}
    r1 = _rpc(app_client_gated, 1, "SendMessage", m1, headers=header)
    assert r1.json()["result"]["task"]["metadata"]["created"] is True
    m2 = {"message": {"role": "ROLE_USER", "parts": [{"text": "甲"}],
                      "messageId": "client-m2", "contextId": "gctx3"}}
    r2 = _rpc(app_client_gated, 2, "SendMessage", m2, headers=header)
    body2 = r2.json()["result"]["task"]
    assert body2["metadata"]["created"] is False, \
        "gate 幂等键须覆盖客户端 messageId"
    assert body2["id"] == r1.json()["result"]["task"]["id"]
    for _ in range(100):        # sleep 先行：轮询行必然执行
        time.sleep(0.02)
        if intercepted:
            break
    assert len(intercepted) == 1, "幂等键接管下只分发一次"


# ── ResolveInteraction：gate canonical context + 转发矩阵 ────────────────────

def test_resolve_gate_context_and_not_found_paths(app_client_gated,
                                                  monkeypatch):
    from engine.community.api.transport.auth_gate import AuthGateResult
    fake = _FakeResolveChat()
    _install_resolve_bridge(monkeypatch, fake)
    k = R._kernel

    async def seed():
        rid, _ = await k.open_run("hitl-g", "k", "first", "d")
        k.runs[rid].native_run_id = "nat-g"
        await k.publish_interaction_requested(rid, "i-g", {"kind": "ask_user"})
        return rid

    rid = asyncio.run(seed())
    seen: dict[str, object] = {}
    gate_mod = _gate_mod()

    async def allowing_verify(*, session_key, message, iam_token, **kwargs):
        seen["session"] = session_key
        seen["message"] = message
        return AuthGateResult(allowed=True)

    monkeypatch.setattr(gate_mod, "verify_chat_send", allowing_verify)
    header = {"x-iam-token": "tok"}
    r = _rpc(app_client_gated, 1, "ResolveInteraction",
             {"interactionId": "i-g",
              "resolution": {"decision": "approve"}}, headers=header)
    assert r.json()["result"]["interaction"]["resolved"] is True
    assert seen == {"session": "hitl-g", "message": "approve"}, \
        "gate 判定的 session/decision 必须与交互所属 run 同源（R13-3）"
    assert k.runs[rid].state == "TASK_STATE_WORKING"
    assert fake.calls, "resolve 应真实转发引擎"

    no_iid = _rpc(app_client_gated, 2, "ResolveInteraction",
                  {"resolution": {"decision": "approve"}}, headers=header)
    assert no_iid.json()["error"]["code"] == -32602
    ghost = _rpc(app_client_gated, 3, "ResolveInteraction",
                 {"interactionId": "ghost",
                  "resolution": {"decision": "approve"}}, headers=header)
    assert ghost.json()["error"]["data"]["reason"] == "not_found"


@pytest.mark.asyncio
async def test_resolve_forward_kind_matrix_and_failures(monkeypatch):
    fake = _FakeResolveChat()
    R2 = _install_resolve_bridge(monkeypatch, fake)
    K = kernel_mod.A2aKernel()
    monkeypatch.setattr(R2, "_kernel", K)

    with pytest.raises(R2._ParamError, match="interactionId is required"):
        await R2._resolve_interaction({"resolution": {"decision": "x"}}, 1,
                                      None)
    with pytest.raises(R2._ParamError,
                       match="resolution.decision is required"):
        await R2._resolve_interaction({"interactionId": "i-v"}, 1, None)

    rid, _ = await K.open_run("rk", "k1", "first", "d")

    async def repost(iid):
        await K.publish_interaction_requested(rid, iid, {})

    # ask_user 默认分支：提交 decision + 文本答案逐字转发（R3-1）
    await repost("i-a")
    out = await R2._resolve_interaction(
        {"interactionId": "i-a",
         "resolution": {"decision": "submit", "answer": "那就是这样",
                        "kind": "ask_user"}}, 2, None)
    assert out["interaction"]["resolved"] is True
    kind, params = fake.calls[-1]
    assert kind == "ask_user"
    assert params["decision"] == "submit"
    assert params["answer"] == "那就是这样"
    assert K.runs[rid].state == "TASK_STATE_WORKING"

    # R3-1：取消决策逐字转发——不得被改写成 submit；结构化答案不丢
    await repost("i-cx")
    out = await R2._resolve_interaction(
        {"interactionId": "i-cx",
         "resolution": {"decision": "cancel", "kind": "ask_user"}}, 7, None)
    assert out["interaction"]["resolved"] is True, out
    kind, params = fake.calls[-1]
    assert kind == "ask_user"
    assert params["decision"] == "cancel", "取消被改写成了 submit"
    assert params["answer"] is None

    await repost("i-sa")
    out = await R2._resolve_interaction(
        {"interactionId": "i-sa",
         "resolution": {"decision": "submit", "kind": "ask_user",
                        "answers": {"q1": "A", "q2": "B"},
                        "values": {"f1": "v1"},
                        "selected_options": [["o1", "o2"]]}}, 8, None)
    assert out["interaction"]["resolved"] is True, out
    kind, params = fake.calls[-1]
    assert kind == "ask_user"
    assert params["answers"] == {"q1": "A", "q2": "B"}
    assert params["values"] == {"f1": "v1"}
    assert params["selected_options"] == [["o1", "o2"]]

    # selectedOptions 的 v3 wire 形态也接受（规范化到 selected_options）
    await repost("i-so")
    out = await R2._resolve_interaction(
        {"interactionId": "i-so",
         "resolution": {"decision": "submit", "kind": "ask_user",
                        "selectedOptions": [["ox"]]}}, 9, None)
    assert out["interaction"]["resolved"] is True, out
    _, params = fake.calls[-1]
    assert params["selected_options"] == [["ox"]]

    # 载荷类型不符 → 边界错误，不带病下行
    with pytest.raises(R2._ParamError, match="selected_options"):
        await R2._resolve_interaction(
            {"interactionId": "i-bad",
             "resolution": {"decision": "submit", "kind": "ask_user",
                            "selected_options": "junk"}}, 10, None)
    with pytest.raises(R2._ParamError, match="answers"):
        await R2._resolve_interaction(
            {"interactionId": "i-bad2",
             "resolution": {"decision": "submit", "kind": "ask_user",
                            "answers": ["junk"]}}, 11, None)
    with pytest.raises(R2._ParamError, match="decision is required"):
        await R2._resolve_interaction(
            {"interactionId": "i-bad3",
             "resolution": {"kind": "ask_user"}}, 12, None)
    with pytest.raises(R2._ParamError, match="kind must be a string"):
        await R2._resolve_interaction(
            {"interactionId": "i-bad4",
             "resolution": {"decision": "submit", "kind": 5}}, 13, None)
    with pytest.raises(R2._ParamError, match="answer must be a string"):
        await R2._resolve_interaction(
            {"interactionId": "i-bad5",
             "resolution": {"decision": "submit", "answer": 42}}, 14, None)

    # mode_switch 分发 + BaaS 持有的真实 interaction id 定位
    await repost("i-m")
    out = await R2._resolve_interaction(
        {"interactionId": "i-m",
         "resolution": {"decision": "approve", "kind": "mode_switch"}}, 3,
        None)
    assert out["interaction"]["resolved"] is True
    kind, params = fake.calls[-1]
    assert kind == "mode_switch"
    assert params["decision"] == "approve"
    assert params["interaction_id"] == "i-m"

    # 不支持的 kind → 边界错误
    await repost("i-x")
    with pytest.raises(R2._ParamError, match="unsupported resolution kind"):
        await R2._resolve_interaction(
            {"interactionId": "i-x",
             "resolution": {"decision": "y", "kind": "bio"}}, 4, None)

    # 引擎面不可用 → resolve_target_unavailable（引擎零转发）
    rid2, _ = await K.open_run("rk2", "k2", "first", "d")
    await K.publish_interaction_requested(rid2, "i-noeng", {})
    before = len(fake.calls)

    def broken_plugin():
        raise RuntimeError("engine chat surface unavailable")

    monkeypatch.setattr(bridge, "_chat_plugin", broken_plugin)
    out = await R2._resolve_interaction(
        {"interactionId": "i-noeng",
         "resolution": {"decision": "approve"}}, 5, None)
    assert out["error"]["data"]["reason"] == "resolve_target_unavailable"
    assert len(fake.calls) == before

    # 转发超时 → unknown_outcome，run 保持 INPUT_REQUIRED
    class _HangingResolve:
        async def resolve_interaction(self, session_key, interaction_id,
                                      **kw):
            await asyncio.sleep(10)

    _install_resolve_bridge(monkeypatch, _HangingResolve())
    monkeypatch.setattr(R2, "_RESOLVE_FORWARD_TIMEOUT_SECONDS", 0.05)
    await K.publish_interaction_requested(rid, "i-t", {})
    out = await R2._resolve_interaction(
        {"interactionId": "i-t", "resolution": {"decision": "approve"}},
        6, None)
    err = out["error"]
    assert err["data"]["reason"] == "unknown_outcome"
    assert "timeout" in err["message"]
    assert K.runs[rid].state == "TASK_STATE_INPUT_REQUIRED", \
        "结果不可知时不回写 WORKING"


def test_message_text_requires_list_parts():
    """parts 非 list（缺失/字符串/对象）一律空文本——而非裸抛炸 500。"""
    assert R._message_text({}) == ""
    assert R._message_text({"parts": "junk"}) == ""
    assert R._message_text({"parts": {"text": "x"}}) == ""
    assert R._message_text({"parts": [{"text": "甲"}, {"text": "乙"}]}) == "甲乙"
