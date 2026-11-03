"""test bot a2a lifecycle; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
    make_card_app,
)
from a2a_test_support_base import (
    stream_server as stream_server,
)
from a2a_test_support_engines import (
    _run_terminal_scenario,
)
from a2a_test_support_lifecycle import (
    _MALFORMED_CARDS,
    _dead_rpc_url,
    _FakeV3KingineTransport,
    make_card_down_app,
    make_malformed_card_app,
)
from fastapi import FastAPI, Request
from fastapi.responses import Response

from secbaas.community.api.bot_interaction._models import InteractionResolution
from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError,
    BotA2aClient,
)


def test_attachment_hitl_answer_routes_via_v3_and_run_resumes():
    """P1-4 闭环：附件请求 → v3 run → interaction.requested → 用户答复 →
    答复发到 v3 专用连接（A2A kernel 无此 run）→ 同一 v3 run 恢复收终态。

    注入面说明：v3 侧（engine v3 WS 面）本测试树没有真实服务端实现，
    BotWebSocketClient 构造点以 duck 替身注入——归属路由/handler 桥/
    回收清表全为被测真实代码；a2a 侧探测走真 uvicorn TCP。"""
    from unittest.mock import MagicMock, patch

    from secbaas.community.api.bot_interaction import InteractionRequestedResult
    from secbaas.community.core.service.bot_interaction import InteractionDispatch
    from secbaas.community.core.service.bot_run._async_chat_client import (
        AsyncChatClient,
    )

    srv = LiveServer(make_card_app("JSONRPC", "1.0"))
    try:

        async def scenario():
            service = MagicMock()
            service.record_requested.return_value = InteractionRequestedResult(
                baas_interaction_id="BAAS-1", created=True
            )
            resolution = InteractionResolution(kind="exec", decision="allow-once")
            service.claim_for_dispatch.return_value = InteractionDispatch(
                session_key="hitl-1",
                interaction_id="i-1",
                resolution=resolution,
            )
            acc = AsyncChatClient(
                uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                transport_mode="on",
                max_retries=0,
                interaction_service=service,
            )
            hello = await acc.connect()
            assert acc.transport_name == "a2a", hello

            async def spy_resolve(**kwargs: Any) -> Any:
                raise AssertionError("answer leaked to a2a transport")

            assert acc._client is not None
            acc._client.interaction_resolve = spy_resolve

            with patch(
                "secbaas.community.core.service.bot_run._async_chat_client_ws.BotWebSocketClient",
                _FakeV3KingineTransport,
            ):
                task = asyncio.create_task(
                    acc.send_message(
                        "approve?",
                        session_key="hitl-1",
                        attachments=[{"kind": "screenshot"}],
                        auth_token="tok-live",
                    )
                )
                await asyncio.sleep(0.05)  # reroute 发生
                v3 = acc._chat_transport.fallback_client
                assert v3 is not None, "附件请求未 reroute 到 v3 fallback"
                assert v3.chat_send_calls, "chat_send 未打到 v3"
                assert v3.chat_send_calls[0][0] == "hitl-1"
                assert v3.chat_send_calls[0][1]["attachments"] == [
                    {"kind": "screenshot"}
                ]

                # 引擎经 v3 推同一 run 的 interaction.requested
                v3.emit(
                    "interaction.requested",
                    {
                        "sessionKey": "hitl-1",
                        "interactionId": "i-1",
                        "kind": "exec",
                        "options": [{"label": "Once", "decision": "allow-once"}],
                    },
                )
                # 用户答复 → 派发必须走 v3 连接（A2A 侧断言已布雷：必炸）
                for _ in range(50):
                    if v3.interaction_resolve_calls:
                        break
                    await asyncio.sleep(0.05)
                assert v3.interaction_resolve_calls, "答复未路由到产生 run 的 v3 连接"
                # 用户答复已应答后，服务端记取 exchange
                assert service.claim_for_dispatch.call_count >= 1

                # 同一 v3 run 恢复收尾：resolved + 终稿
                v3.emit(
                    "interaction.resolved",
                    {"sessionKey": "hitl-1", "interactionId": "i-1"},
                )
                v3.emit(
                    "chat",
                    {
                        "sessionKey": "hitl-1",
                        "state": "final",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "resumed-after-approval"}
                            ],
                        },
                    },
                )
                content, _events = await asyncio.wait_for(task, 5)
                assert content == "resumed-after-approval"
                assert "hitl-1" not in acc._chat_transport.routed_sessions
                await acc.close()
                return service

        svc = _run(scenario())
        svc.record_engine_exchange.assert_called_once()
        assert svc.record_engine_exchange.call_args.kwargs["interaction_id"] == "i-1"
    finally:
        srv.stop()


def test_card_probe_http_failure_raises_a2a_unavailable():
    srv = LiveServer(make_card_down_app())
    try:
        with pytest.raises(A2aUnavailableError, match="capability probe failed"):
            _run(_connect_async(srv.base))
    finally:
        srv.stop()


@pytest.mark.parametrize("case,payload", _MALFORMED_CARDS)
def test_card_structural_malform_converts_to_a2a_unavailable(case, payload):
    srv = LiveServer(make_malformed_card_app(payload))
    try:
        with pytest.raises(A2aUnavailableError, match="agent card|malformed"):
            _run(_connect_async(srv.base))
    finally:
        srv.stop()


def test_double_connect_is_rejected(stream_server):
    async def scenario():
        client = await _connect_async(stream_server.base)
        try:
            await client.connect()
        finally:
            await client.close()

    with pytest.raises(RuntimeError, match="Already connected"):
        _run(scenario())


def test_features_property_is_card_features_face(stream_server):
    async def scenario():
        client = await _connect_async(stream_server.base)
        # A2A 卡无 features 段——面恒为 None，不得伪造能力
        assert client.features is None
        assert client.server_info is not None
        await client.close()

    _run(scenario())


def test_connect_probes_via_injected_http_factory():
    srv = LiveServer(make_card_app("JSONRPC", "1.0"))
    try:

        async def scenario():
            made: list[httpx.AsyncClient] = []

            def factory() -> httpx.AsyncClient:
                built = httpx.AsyncClient(timeout=5.0, proxy=None)
                made.append(built)
                return built

            client = BotA2aClient(base_url=srv.base, http_transport_factory=factory)
            hello = await client.connect()
            assert hello["protocol"] == 1, hello
            assert made, "探测请求必须经注入的 http 工厂出站"
            await client.close()

        _run(scenario())
    finally:
        srv.stop()


def test_close_tolerates_raising_disconnect_callback():
    """断连回调抛错不得炸 close——close 自身收口语义必须成立。"""
    srv = LiveServer(make_card_app("JSONRPC", "1.0"))
    try:

        async def scenario():
            client = await _connect_async(srv.base)

            def boom(event_name: str, payload: dict) -> None:
                raise RuntimeError("disconnect handler exploded")

            client.on_disconnect(boom)
            await client.close()
            assert client.connected is False, "回调抛错后 close 仍须收口"

        _run(scenario())
    finally:
        srv.stop()


def test_chat_send_not_connected_is_not_fake_ok():
    async def scenario():
        client = BotA2aClient(base_url="http://unused")
        return await client.chat_send(session_key="s", message="hi")

    out = _run(scenario())
    assert out["ok"] is False
    assert out["error"]["code"] == "NOT_CONNECTED"


def test_chat_send_transport_error_is_honest():
    """出站连接失败 → TRANSPORT_ERROR + 原始错误，不得静默。"""

    async def scenario():
        client = BotA2aClient(base_url="http://unused")
        client._connected = True
        client.rpc_url = _dead_rpc_url()
        return await client.chat_send(session_key="s", message="hi")

    out = _run(scenario())
    assert out["ok"] is False
    assert out["error"]["code"] == "TRANSPORT_ERROR"
    assert out["error"]["message"], "透传原始错误供排障"


def test_chat_send_non_json_error_body_still_reports_http_status():
    """非 SSE 且错误体非 JSON：HTTP 码仍须显式，message 留空不造垃圾。"""

    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {
            "supportedInterfaces": [
                {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
            ]
        }

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        await request.json()
        return Response("upstream exploded", status_code=400, media_type="text/plain")

    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            out = await client.chat_send(session_key="s", message="hi")
            await client.close()
            return out

        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_400"
    assert out["error"]["message"] == ""


def test_message_member_dispatches_user_echo_chat_event():
    seen = _run_terminal_scenario(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"message": {"parts": [{"text": "user echo"}]}},
            },
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "statusUpdate": {
                        "taskId": "t1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "metadata": {"final": "true", "seq": 2},
                    }
                },
            },
        ]
    )
    echoes = [p for p in seen if p.get("state") == "message"]
    assert len(echoes) == 1, f"message 成员未按用户回声出站: {seen}"
    assert echoes[0]["sessionKey"] == "s"
    assert echoes[0]["message"]["role"] == "user"
    assert echoes[0]["message"]["content"][0]["text"] == "user echo"
    # runId 是 BaaS 自己的 rid（messageId/幂等键语义），非引擎 taskId
    assert echoes[0]["runId"].startswith("s:")
    assert any(p.get("state") == "final" for p in seen)


def test_second_final_frame_is_suppressed_after_first_terminal():
    seen = _run_terminal_scenario(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "statusUpdate": {
                        "taskId": "t1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "metadata": {"final": "true", "seq": 1},
                    }
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "statusUpdate": {
                        "taskId": "t1",
                        "status": {"state": "TASK_STATE_COMPLETED"},
                        "metadata": {"final": "true", "seq": 2},
                    }
                },
            },
        ]
    )
    finals = [p for p in seen if p.get("state") == "final"]
    assert len(finals) == 1, f"终态帧不得二次投递: {seen}"


def test_dispatch_awaits_async_handlers_without_breaking_pipeline():
    async def scenario():
        client = BotA2aClient(base_url="http://unused")
        seen: list[tuple[str, Any]] = []

        async def chat_handler(payload: dict) -> None:
            seen.append(("chat", payload["k"]))

        async def wildcard(name: str, payload: dict) -> None:
            seen.append(("wildcard", name))

        client.on_event("chat", chat_handler)
        client.on_event("*", wildcard)
        await client._dispatch("chat", {"k": "v"})
        return seen

    seen = _run(scenario())
    assert ("chat", "v") in seen, f"async 精确 handler 未被 await: {seen}"
    assert ("wildcard", "chat") in seen, "精确 handler 完成前不得阻断通配"


def test_dispatch_swallows_handler_errors_without_breaking_wildcard():
    async def scenario():
        client = BotA2aClient(base_url="http://unused")
        seen: list[str] = []

        async def boom(payload: dict) -> None:
            raise RuntimeError("handler blew up")

        async def wildcard(name: str, payload: dict) -> None:
            seen.append(name)
            raise ValueError("wildcard blew up")

        client.on_event("chat", boom)
        client.on_event("*", wildcard)
        await client._dispatch("chat", {"k": 1})  # 双双抛错——不得上浮
        return seen

    seen = _run(scenario())
    assert seen == ["chat"], "精确 handler 抛错后通配仍须执行"
