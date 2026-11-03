"""test bot a2a terminal; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio

from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
)
from a2a_test_support_engines import (
    _run_terminal_scenario,
    make_gate_send_and_cancel_app,
    make_proxypass_engine_app,
)

from secbaas.community.api.bot_interaction._models import InteractionResolution
from tests.utils.chat_transport import make_chat_client


def test_terminal_error_reaches_consumer_on_wire_error_frame():
    """SSE error 帧分支：error 帧到达 → handler 必须恰好收到一次 error 终态。
    （P1-2 复现：_emit_terminal_error 漏 await → coroutine 从未启动。）"""
    seen = _run_terminal_scenario(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {}},
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32000,
                    "message": "gate denied",
                    "data": {"reason": "auth_required"},
                },
            },
        ]
    )
    errors = [p for p in seen if p.get("state") == "error"]
    assert len(errors) == 1, f"error terminal missing/duplicated: {seen}"
    assert errors[0].get("errorCode") == "auth_required"
    assert errors[0].get("errorMessage") == "gate denied"


def test_terminal_error_reaches_consumer_on_eof_without_terminal():
    """EOF 无终态分支：流在终态前结束 → 必须收恰好一次
    STREAM_CLOSED_WITHOUT_TERMINAL error（消费方不得悬挂）。"""
    seen = _run_terminal_scenario(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "statusUpdate": {
                        "taskId": "t1",
                        "status": {"state": "TASK_STATE_WORKING"},
                        "metadata": {"seq": 1},
                    }
                },
            }
        ]
    )
    errors = [p for p in seen if p.get("state") == "error"]
    assert len(errors) == 1, f"no terminal on EOF: {seen}"
    assert errors[0].get("errorCode") == "STREAM_CLOSED_WITHOUT_TERMINAL"


def test_terminal_error_reaches_consumer_on_stream_exception():
    """消费异常分支：行 JSON 解析抛错 → 必须收恰好一次 STREAM_ERROR。"""
    seen = _run_terminal_scenario(
        [
            "data: {not-json",
        ]
    )
    errors = [p for p in seen if p.get("state") == "error"]
    assert len(errors) == 1, f"no terminal on stream exception: {seen}"
    assert errors[0].get("errorCode") == "STREAM_ERROR"


def test_terminal_emitted_dedup_across_branches():
    """首见终态后后续异常/EOF 不得二次投递（terminal_emitted 单次语义）。"""
    seen = _run_terminal_scenario(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32000,
                    "message": "first error",
                    "data": {"reason": "a2a_error"},
                },
            },
            "data: {garbage-after-terminal",
        ]
    )
    errors = [p for p in seen if p.get("state") == "error"]
    assert len(errors) == 1, f"terminal leaked twice: {seen}"
    assert errors[0].get("errorMessage") == "first error"


def test_proxypass_route_prefix_survives_to_card_and_rpc():
    """P1-1 复现：wss://proxy/proxypass/<target>/api/openclaw/ws 转换出的
    A2A base 必须保留路由前缀——card GET 与 RPC 都打到 {prefix}/ 而非代理根
    （转换丢前缀时 grey 永远回退、on 直接连不上）。"""
    prefix = "/proxypass/sbx-a2a-1"
    srv = LiveServer(make_proxypass_engine_app(prefix))
    try:

        async def scenario():

            acc = make_chat_client(
                uri=f"ws://127.0.0.1:{srv.port}{prefix}/api/openclaw/ws",
                transport_mode="on",
                max_retries=0,
            )
            assert acc._chat_transport.mode == "on"
            hello = await acc.connect()
            assert acc.transport_name == "a2a", (
                f"probing must hit prefixed card: hello={hello}"
            )
            content, _events = await acc.send_message(session_key="s", message="hi")
            base_url = acc._chat_transport._client.base_url
            await acc.close()
            return base_url

        base_url = _run(scenario())
        assert base_url == f"http://127.0.0.1:{srv.port}{prefix}"
    finally:
        srv.stop()


def test_gate_cancel_uses_run_iam_token():
    """P1-5 复现：send 成功（带 token）→ CancelTask 必须携带该 run 记录的
    X-IAM-Token（只带 proxypass 头会 auth_required，原本 abort 必败）。"""
    app, state = make_gate_send_and_cancel_app(mode="cancel")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            ack = await client.chat_send(
                session_key="s", message="hi", auth_token="tok-live"
            )
            assert ack["ok"] is True
            await asyncio.sleep(0.15)  # WORKING 帧 → engine_task_id 就位
            out = await client.chat_abort(session_key="s")
            await asyncio.sleep(0.2)
            await client.close()
            return out

        out = _run(scenario())
        assert out["ok"] is True, f"CancelTask gate-rejected (no run token): {out}"
        assert state["cancel_headers"]["x-iam-token"] == "tok-live"
    finally:
        srv.stop()


def test_gate_resolve_uses_run_iam_token_and_run_resumes():
    """P1-5 复现：send（带 token）→ interaction.requested → ResolveInteraction
    带 run token 答复成功，同 run 经流恢复终态（不关 Gate、不打引擎补丁）。"""
    app, state = make_gate_send_and_cancel_app(mode="resolve")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            seen: list[dict] = []
            client.on_event("interaction.requested", lambda p: seen.append(("req", p)))
            client.on_event(
                "chat",
                lambda p: (
                    seen.append(("chat", p))
                    if p.get("state") in ("final", "error")
                    else None
                ),
            )
            ack = await client.chat_send(
                session_key="s", message="hi", auth_token="tok-live"
            )
            assert ack["ok"] is True
            await asyncio.sleep(0.15)  # INPUT_REQUIRED 帧入流
            exchange = await client.interaction_resolve(
                interaction_id="i-gate",
                resolution=InteractionResolution(kind="ask_user", decision="submit"),
                session_key="s",
            )
            await asyncio.sleep(0.3)  # 流继续 → 终态
            await client.close()
            return exchange, seen

        exchange, seen = _run(scenario())
        assert exchange.accepted is True, f"resolve gate-rejected: {exchange}"
        assert state["resolve_headers"]["x-iam-token"] == "tok-live"
        assert any(n == "req" for n, _ in seen), "INTERACTION 请求未入流"
        finals = [p for n, p in seen if n == "chat" and p.get("state") == "final"]
        assert finals, f"同 run 未恢复终态: {seen}"
    finally:
        srv.stop()
