"""test bot a2a abort responses; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio

from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
)
from a2a_test_support_cancel import (
    _cancel_capable_card,
)
from a2a_test_support_cancel_response import (
    make_cancel_response_app,
)
from a2a_test_support_lifecycle import (
    _dead_rpc_url,
)
from fastapi.responses import JSONResponse, Response

from secbaas.community.core.service.bot_run._bot_a2a_client import (
    BotA2aClient,
    _RunCtx,
)


def test_abort_without_card_snapshot_is_honestly_unsupported():
    """能力卡快照缺失（未探测过/被覆写）——abort 诚实 CANCEL_UNSUPPORTED，
    绝不从缺席的卡上猜引擎仍可取消。"""

    async def scenario():
        client = BotA2aClient(base_url="http://unused")
        client._connected = True
        ctx = _RunCtx("rid-noc", "s-noc")
        ctx.engine_task_id = "eng-learned"  # 有引擎侧 id 也没用——无卡即不支持
        client._runs["rid-noc"] = ctx
        client._session_runs["s-noc"] = "rid-noc"
        return await client.chat_abort("s-noc")

    out = _run(scenario())
    assert out["ok"] is False
    assert out["a2a"] is True
    assert out["error"]["code"] == "CANCEL_UNSUPPORTED"


def test_abort_untracked_run_cuts_local_stream_not_send_cancel():
    """引擎声明可取消但 run 的 taskId 已确定学不到（流终结置位身份事件、
    无 ID——R7-F1 后"尚未就位"窗口改为等待身份而非判不归属）：不盲发
    CancelTask——RUN_NOT_TRACKED + 本地断流（消费方立即脱困，不等收尾）。"""

    async def scenario():
        client = BotA2aClient(base_url="http://unused")
        client._connected = True
        client._server_info = {"server": {"card": _cancel_capable_card()}}
        ctx = _RunCtx("rid-cut", "s-cut")
        ctx.auth_token = "OPEN_API:" + "NOT_PROVIDED"  # 哨兵值等值拼接（规则口径）
        ctx.task = asyncio.create_task(asyncio.sleep(30))
        # 模拟 SSE 消费终结而无 ID（流早夭）：身份事件置位、engine_task_id
        # 仍缺——生产上可达（live 流不会学不到身份却永不置位）。
        ctx.identity_ready.set()
        client._runs["rid-cut"] = ctx
        client._session_runs["s-cut"] = "rid-cut"
        out = await client.chat_abort("s-cut")
        try:
            await ctx.task  # local ctx canceled by abort——test-side收尸
        except asyncio.CancelledError:
            pass
        return out, ctx.task.cancelled()

    out, cut = _run(scenario())
    assert out["ok"] is False
    assert out["error"]["code"] == "RUN_NOT_TRACKED"
    assert cut, "RUN_NOT_TRACKED 必须 fallback 本地断流"


def test_chat_abort_transport_failure_is_honest():
    """CancelTask 通道连接失败 → TRANSPORT_ERROR（非引擎语义、非假成功），
    且 CancelTask 未上 wire（引擎侧零污染）。"""
    app, state = make_cancel_response_app(
        lambda body: JSONResponse({"boom": True}, status_code=500)
    )
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            await client.chat_send(session_key="s", message="hi")
            await asyncio.sleep(0.15)  # WORKING 帧到达 → engine_task_id 就位
            client.rpc_url = _dead_rpc_url()  # 注入传输面故障（引擎本身不可达语义）
            out = await client.chat_abort(session_key="s")
            await client.close()
            return out

        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "TRANSPORT_ERROR"
    assert out["error"]["message"], "透传原始错误供排障"
    assert state["bodies"] == [], "CancelTask 不应已上 wire"


def test_chat_abort_garbage_response_is_malformed_not_fake_ok():
    """200 但响应体非 JSON → CANCEL_MALFORMED 诚实面，不得假成功。"""
    app, state = make_cancel_response_app(
        lambda body: Response("not-json at all", media_type="text/plain")
    )
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
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "CANCEL_MALFORMED"
    assert "unparseable http_200" in out["error"]["message"]
    assert len(state["bodies"]) == 1


def test_chat_abort_unexpected_result_state_is_not_success():
    """result 存在但 state 非 CANCELED（异常乱序/引擎误解）——E4 纪律：
    只有真实契约才算成功。"""
    app, state = make_cancel_response_app(
        lambda body: {
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {
                "task": {
                    "id": body["params"]["taskId"],
                    "status": {"state": "TASK_STATE_COMPLETED"},
                }
            },
        }
    )
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
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "CANCEL_RESULT_INVALID"
    assert "unexpected cancel result" in out["error"]["message"]
