"""test bot a2a cancel; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
import time

from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
)
from a2a_test_support_cancel import (
    _cancel_capable_card,
    make_cancel_app,
)


def test_chat_abort_forwards_cancel_task_to_engine():
    """真转发：wire 上的 taskId = 引擎侧 run id（流上捕获，1c 前 BaaS 只见
    传输层 rid）；成功判 ok；消费方经流收 CANCELED 终态 error，不悬挂。"""
    app, state = make_cancel_app(mode="cancels")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            seen: list[dict] = []
            client.on_event("chat", lambda p: seen.append(p))
            ack = await client.chat_send(session_key="s", message="hi")
            assert ack["ok"] is True
            await asyncio.sleep(0.15)  # WORKING 帧到达 → engine_task_id 就位
            out = await client.chat_abort(session_key="s")
            await asyncio.sleep(0.5)  # CancelTask 语义下引擎经流推终态
            await client.close()
            return out, seen

        out, seen = _run(scenario())
        assert out["ok"] is True, out
        assert out["a2a"] is True
        assert out["task"]["state"] == "TASK_STATE_CANCELED"
        # engineAbort 透传——调用方可观测引擎侧真中止结局
        assert out["engineAbort"] == "confirmed", out
        cancels = [b for b in state["bodies"] if b.get("method") == "CancelTask"]
        assert len(cancels) == 1, f"CancelTask 上 wire {len(cancels)} 次"
        assert cancels[0]["params"]["taskId"] == "eng-1", cancels[0]
        assert cancels[0]["jsonrpc"] == "2.0"
        errors = [p for p in seen if p.get("state") == "error"]
        assert errors, f"no terminal to consumer: {seen}"
        assert errors[0].get("errorCode") == "TASK_STATE_CANCELED"
    finally:
        srv.stop()


def test_chat_abort_passes_through_unconfirmed_outcome():
    """验收 5（R3-3）：非 confirmed 结局经 BaaS 透传不丢失——ok 判定
    只认 state==CANCELED，engineAbort 供调用方告警消费。"""
    app, state = make_cancel_app(mode="cancels", abort_outcome="timeout")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            ack = await client.chat_send(session_key="s", message="hi")
            assert ack["ok"] is True
            await asyncio.sleep(0.15)  # WORKING 帧到达 → engine_task_id 就位
            out = await client.chat_abort(session_key="s")
            await asyncio.sleep(0.4)  # 引擎经流推 CANCELED 终态
            await client.close()
            return out

        out = _run(scenario())
        assert out["ok"] is True, out
        assert out["task"]["state"] == "TASK_STATE_CANCELED"
        assert out["engineAbort"] == "timeout", f"非 confirmed 结局必须原样透传: {out}"
    finally:
        srv.stop()


def test_chat_abort_engine_refusal_is_explicit_not_fake_ok():
    """任务终态语义：引擎拒绝（task_terminal）→ 明确 ok=False；
    本地流不被误杀——消费方按引擎真实走向收 COMPLETED final。"""
    app, state = make_cancel_app(mode="refuse")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            finals: list[dict] = []
            client.on_event(
                "chat",
                lambda p: (
                    finals.append(p) if p.get("state") in ("final", "error") else None
                ),
            )
            await client.chat_send(session_key="s", message="hi")
            await asyncio.sleep(0.15)
            out = await client.chat_abort(session_key="s")
            await asyncio.sleep(0.8)
            await client.close()
            return out, finals

        out, finals = _run(scenario())
        assert out["ok"] is False, out
        assert out["error"]["code"] == "task_terminal"
        assert any(p.get("state") == "final" for p in finals), (
            f"拒绝后本地流被误杀: {finals}"
        )
    finally:
        srv.stop()


def test_chat_abort_transport_error_is_explicit():
    """传输面错误（HTTP 500）同样不假成功——错误码显式 HTTP_500。"""
    app, state = make_cancel_app(mode="http_500")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            await client.chat_send(session_key="s", message="hi")
            await asyncio.sleep(0.15)
            out = await client.chat_abort(session_key="s")
            await client.close()
            return out

        out = _run(scenario())
        assert out["ok"] is False, out
        assert out["error"]["code"] == "HTTP_500"
    finally:
        srv.stop()


def test_chat_abort_malformed_result_is_not_fake_ok():
    """200 但响应畸形（无 result/error、id 不回显）→ CANCEL_MALFORMED，
    不得从畸形响应推导成功（E4 判定纪律）。"""
    app, state = make_cancel_app(mode="malformed")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            await client.chat_send(session_key="s", message="hi")
            await asyncio.sleep(0.15)
            out = await client.chat_abort(session_key="s")
            await client.close()
            return out

        out = _run(scenario())
        assert out["ok"] is False, out
        assert out["error"]["code"] == "CANCEL_MALFORMED"
        assert len(state["bodies"]) == 1
    finally:
        srv.stop()


def test_chat_abort_unknown_run_is_honest_not_fake_ok():
    """无可用引擎 task id（从未开流/已收尾的 session）→ 不盲发、不假 ok：
    RUN_NOT_TRACKED + 零 CancelTask 请求上 wire，本地断流 fallback 保留。"""
    app, state = make_cancel_app(mode="cancels")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            out = await client.chat_abort(session_key="ghost-session")
            await client.close()
            return out

        out = _run(scenario())
        assert out["ok"] is False, out
        assert out["error"]["code"] == "RUN_NOT_TRACKED"
        assert state["bodies"] == [], "无 task id 不得盲发 CancelTask"
    finally:
        srv.stop()


def test_chat_abort_waits_for_engine_identity_before_cancel():
    """R7-F1：ACK 已返回、首帧被闸门挡住（engine_task_id 尚未就位）时取消
    ——归属成立（run 已登记、body task 在途），abort 必须等身份就绪再发
    CancelTask，不得把"尚未读到引擎 ID"误判成"不归属"而只断本地流。"""
    app, state = make_cancel_app(mode="cancels", first_frame_gate=True)
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            ack = await client.chat_send(session_key="s", message="hi")
            assert ack["ok"] is True

            # 无任何 sleep：立即取消——此时 engine_task_id 一定未就位。
            abort_task = asyncio.create_task(client.chat_abort(session_key="s"))

            async def release_first_frame():
                # 给 abort 进入"等身份"的机会再放行首帧（否则又变时序赛跑）。
                await asyncio.sleep(0.2)
                state["first_frame"].set()

            await asyncio.create_task(release_first_frame())
            out = await abort_task
            await asyncio.sleep(0.5)  # CancelTask 语义下引擎经流推终态
            await client.close()
            return out

        out = _run(scenario())
        assert out["ok"] is True, out
        assert out["task"]["state"] == "TASK_STATE_CANCELED"
        cancels = [b for b in state["bodies"] if b.get("method") == "CancelTask"]
        assert len(cancels) == 1, f"CancelTask 上 wire {len(cancels)} 次"
        assert cancels[0]["params"]["taskId"] == "eng-1", cancels[0]
    finally:
        srv.stop()


def test_chat_abort_immediate_after_ack_still_cancels():
    """R7-F1：ACK 后零 sleep 立即取消也要发出 CancelTask——身份就绪/消费
    终结双重置位的事件保证确定性，不靠时序运气（老 suite 全靠 sleep(0.15)
    等 WORKING 帧绕开了这个窗口）。"""
    app, state = make_cancel_app(mode="cancels")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            ack = await client.chat_send(session_key="s", message="hi")
            assert ack["ok"] is True
            out = await client.chat_abort(session_key="s")  # 无 sleep，紧跟 ACK
            await asyncio.sleep(0.5)  # 引擎经流推 CANCELED 终态
            await client.close()
            return out

        out = _run(scenario())
        assert out["ok"] is True, out
        cancels = [b for b in state["bodies"] if b.get("method") == "CancelTask"]
        assert len(cancels) == 1, f"CancelTask 上 wire {len(cancels)} 次"
        assert cancels[0]["params"]["taskId"] == "eng-1", cancels[0]
    finally:
        srv.stop()


def test_chat_abort_after_stream_died_without_identity_is_prompt():
    """R7-F1 边界：流已 EOF 收口、identity 事件由消费终结侧置位——abort
    不得空等 30s 超时，读不到身份就诚实 RUN_NOT_TRACKED 快速返回。"""
    app, state = make_cancel_app(mode="empty")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            ack = await client.chat_send(session_key="s", message="hi")
            assert ack["ok"] is True
            await asyncio.sleep(0.5)  # 消费方收 EOF→STREAM_CLOSED_WITHOUT_TERMINAL
            t0 = time.monotonic()
            out = await client.chat_abort(session_key="s")
            elapsed = time.monotonic() - t0
            await client.close()
            return out, elapsed

        out, elapsed = _run(scenario())
        assert out["ok"] is False, out
        assert out["error"]["code"] == "RUN_NOT_TRACKED", out
        assert elapsed < 5.0, f"abort 悬挂 {elapsed:.1f}s——消费终结未释放身份等待"
        assert state["bodies"] == [], "无 task id 不得盲发 CancelTask"
    finally:
        srv.stop()


def test_chat_abort_identity_wait_times_out_and_cuts_local_stream(monkeypatch):
    """R7-F1 上限分支：病态活流（连接活着但一个帧都不发）——身份等待超时
    后落回诚实 RUN_NOT_TRACKED + 本地断流，不无限悬挂 abort。"""

    async def scenario():
        from secbaas.community.core.service.bot_run import _bot_a2a_client as mod

        monkeypatch.setattr(mod, "_CANCEL_IDENTITY_TIMEOUT_SECONDS", 0.05)
        client = mod.BotA2aClient(base_url="http://unused")
        client._connected = True
        client._server_info = {"server": {"card": _cancel_capable_card()}}
        ctx = mod._RunCtx("rid-wait", "s-wait")
        ctx.auth_token = "OPEN_API:" + "NOT_PROVIDED"
        ctx.task = asyncio.create_task(asyncio.sleep(30))
        # identity_ready 永不置位（活流不发帧）
        client._runs["rid-wait"] = ctx
        client._session_runs["s-wait"] = "rid-wait"

        t0 = time.monotonic()
        out = await client.chat_abort("s-wait")
        elapsed = time.monotonic() - t0
        try:
            await ctx.task
        except asyncio.CancelledError:
            pass
        return out, elapsed, ctx.task.cancelled()

    out, elapsed, cut = _run(scenario())
    assert out["ok"] is False, out
    assert out["error"]["code"] == "RUN_NOT_TRACKED", out
    assert elapsed < 5.0, f"身份等待未走上限就绪/失败（{elapsed:.1f}s）"
    assert cut, "超时后必须 fallback 本地断流"


def test_abort_routes_with_the_run_b3_and_off_modes():
    """编排层归属路由：B3 附件 reroute 的 session 的 abort 随 run 走 v3
    专用连接；A2A 正常 session 走 A2A 客户端；off/v3 模式永远 self._client
    （原路径不动，路由表不干扰）。"""
    from secbaas.community.core.service.bot_run._async_chat_client import (
        AsyncChatClient,
    )

    calls: list[tuple] = []

    class _FakeTransport:
        connected = True

        def __init__(self, tag: str):
            self.tag = tag

        async def chat_abort(self, session_key, run_id=None):
            calls.append((self.tag, session_key, run_id))
            return {"ok": True}

    async def scenario():
        acc = AsyncChatClient(uri="ws://unused", transport_mode="grey")
        acc._chat_transport._current_name = "a2a"
        acc._client = _FakeTransport("a2a")
        acc._chat_transport.fallback_client = _FakeTransport("v3")
        acc._chat_transport.routed_sessions["s-att"] = "v3"
        await acc.chat_abort("s-att", "r1")  # B3 reroute 过的 run
        await acc.chat_abort("s-normal")  # A2A 正常 session

        acc2 = AsyncChatClient(uri="ws://unused")
        acc2._client = _FakeTransport("v3-ws")
        assert not hasattr(acc2._chat_transport, "routed_sessions")
        await acc2.chat_abort("s-att")

    _run(scenario())
    assert calls == [
        ("v3", "s-att", "r1"),
        ("a2a", "s-normal", None),
        ("v3-ws", "s-att", None),
    ]
