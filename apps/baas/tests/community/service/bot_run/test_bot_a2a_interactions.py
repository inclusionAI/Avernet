"""test bot a2a interactions; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
from typing import Any

from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
    make_card_app,
    make_stream_app,
)
from a2a_test_support_base import (
    stream_server as stream_server,
)
from a2a_test_support_wire import (
    _consume_wire_events,
    _real_wire_stream,
    _resolve_app_with,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from secbaas.community.api.bot_interaction._models import InteractionResolution
from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError,
    BotA2aClient,
)
from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionResolveExchange,
)


def test_interaction_resolve_success_and_conflict(stream_server):
    async def ok_case():
        client = await _connect_async(stream_server.base)
        return await client.interaction_resolve(
            interaction_id="i-1", resolution=InteractionResolution(decision="approve")
        )

    async def conflict_case():
        client = await _connect_async(stream_server.base)
        return await client.interaction_resolve(
            interaction_id="i-1", resolution=InteractionResolution(decision="deny")
        )

    out = _run(ok_case())
    assert isinstance(out, EngineInteractionResolveExchange)
    assert out.accepted is True and out.error_message is None
    assert out.request["id"] == out.response["id"]  # R17-6 校验

    conflict = _run(conflict_case())
    assert isinstance(conflict, EngineInteractionResolveExchange)
    assert conflict.accepted is False
    assert conflict.error_message is not None


def test_interaction_resolve_malformed_response():
    class _BrokenApp(FastAPI):
        pass

    app = _BrokenApp()

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
        return {"wrong": "shape"}  # 无 id、无 result/error

    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            return await client.interaction_resolve(
                interaction_id="i-9",
                resolution=InteractionResolution(decision="approve"),
            )

        out = _run(scenario())
        assert out.accepted is False
        assert out.error_message == "malformed or mismatched response"
    finally:
        srv.stop()


def test_capability_matrix():
    for binding, version, should_fail in [
        ("JSONRPC", "1.0", False),
        ("HTTP+JSON", "1.0", True),
        ("JSONRPC", "0.3.0", True),
    ]:
        srv = LiveServer(make_card_app(binding, version))
        try:

            async def probe():
                client = BotA2aClient(base_url=srv.base)
                return await client.connect()

            if should_fail:
                try:
                    _run(probe())
                    raise AssertionError(f"{binding}/{version} must be rejected")
                except A2aUnavailableError:
                    pass
            else:
                hello = _run(probe())
                assert hello["protocol"] == 1
        finally:
            srv.stop()


def test_attachments_rejected_without_silence(stream_server):
    async def scenario():
        client = await _connect_async(stream_server.base)
        return await client.chat_send(
            session_key="s", message="hi", attachments=[{"type": "image"}]
        )

    out = _run(scenario())
    assert out["ok"] is False
    assert out["error"]["code"] == "ATTACHMENTS_UNSUPPORTED"


def test_metadata_flattened_on_wire(stream_server):
    captured: dict[str, Any] = {}
    app2 = make_stream_app()
    # 用自定义 app 捕获：直接在现有 server 上补 pin 路由不可行——
    # 改用新 LiveServer 捕获请求体

    cap_app = FastAPI()

    @cap_app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {
            "supportedInterfaces": [
                {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
            ]
        }

    @cap_app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        captured.update(body)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32600}}
        )

    srv = LiveServer(cap_app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            await client.chat_send(
                session_key="s",
                message="hi",
                timeout_ms=4500,
                app_id="app-77",
                chat_metadata={"permissionMode": "bypassPermissions", "source": "e2e"},
            )

        _run(scenario())
        meta = captured["params"]["metadata"]
        assert meta["permissionMode"] == "bypassPermissions"  # 平铺键
        assert meta["source"] == "e2e"
        assert meta["appId"] == "app-77"
        assert meta["timeoutMs"] == "4500"
    finally:
        srv.stop()


def test_interaction_requested_roundtrip_lossless():
    """R18-1 复现的测试(先红)：kernel→wire→B1 真往返——
    questions/kind/allowedDecisions 不得丢失。"""

    from engine_shim import kernel as engine_kernel

    payload = {
        "interactionId": "i-1",
        "kind": "ask_user",
        "runId": "r1",
        "questions": [{"q": "选哪个", "options": ["a", "b"]}],
        "allowedDecisions": ["approve", "deny"],
        "sessionKey": "s",
    }

    async def produce_kernel_events():
        k = engine_kernel.A2aKernel()
        rid, _ = await k.open_run("s", "k1", "first", "d")
        await k.publish_interaction_requested(rid, "i-1", payload)
        backlog, _ = await k.subscribe(rid, since_seq=0)
        return backlog

    kernel_events = _run(produce_kernel_events())
    received: list[tuple[str, dict]] = []
    client = BotA2aClient(base_url="http://unused")
    client._connected = True
    client.on_event(
        "interaction.requested",
        lambda p, _n="interaction.requested": received.append((_n, p)),
    )
    _consume_wire_events(client, _real_wire_stream(kernel_events))

    assert received, "interaction.requested 未派发（R18-1 需先红）"
    out = received[0][1]
    assert out.get("questions") == payload["questions"], f"questions 丢失: {out}"
    assert out.get("kind") == "ask_user", f"kind 丢失: {out}"
    assert out.get("allowedDecisions") == payload["allowedDecisions"], (
        f"allowedDecisions 丢失: {out}"
    )


def test_approval_resolved_keeps_interaction_identity():
    """R18-1b 复现的测试(先红)：approval_resolved 只带 approvalId →
    必须以 interaction.resolved **事件身份**出站（不能变普通 WORKING chat）。"""

    from engine_shim import kernel as engine_kernel

    async def produce_kernel_events():
        k = engine_kernel.A2aKernel()
        rid, _ = await k.open_run("s", "k2", "first", "d")
        await k.publish_interaction_resolved(
            rid,
            {"approvalId": "apr-1", "state": "approval_resolved", "sessionKey": "s"},
        )
        backlog, _ = await k.subscribe(rid, since_seq=0)
        return backlog

    kernel_events = _run(produce_kernel_events())
    received: list[tuple[str, dict]] = []
    client = BotA2aClient(base_url="http://unused")
    client._connected = True
    client.on_event(
        "interaction.resolved", lambda p: received.append(("interaction.resolved", p))
    )
    client.on_event("chat", lambda p: received.append(("chat", p)))
    _consume_wire_events(client, _real_wire_stream(kernel_events))

    ir = [p for n, p in received if n == "interaction.resolved"]
    assert ir, (
        f"approval_resolved 变成普通 chat: {[(n, p.get('state')) for n, p in received]}"
    )
    assert ir[0].get("interactionId") == "apr-1"  # 身份保真


def test_partial_resolution_frame_dispatches_by_marker_not_state():
    """R5-2 复现（先红）：多 pending 部分决议的 resolved 帧携带
    INPUT_REQUIRED——必须按 meta.kind 分层派发为 interaction.resolved，
    不得把"已决议"误派发成新的 interaction.requested（审批面板回弹）。"""

    from engine_shim import kernel as engine_kernel

    async def produce_kernel_events():
        k = engine_kernel.A2aKernel()
        rid, _ = await k.open_run("s", "k-r5", "first", "d")
        await k.publish_interaction_requested(
            rid, "apr-A", {"approvalId": "apr-A", "kind": "exec"}
        )
        await k.publish_interaction_requested(rid, "i-B", {"kind": "ask_user"})
        # 部分决议：approval 回流只解决 apr-A（i-B 仍未决）
        await k.publish_interaction_resolved(
            rid, {"approvalId": "apr-A", "state": "approval_resolved"}
        )
        backlog, _ = await k.subscribe(rid, since_seq=0)
        return backlog

    kernel_events = _run(produce_kernel_events())
    received: list[tuple[str, dict]] = []
    client = BotA2aClient(base_url="http://unused")
    client._connected = True
    client.on_event(
        "interaction.resolved", lambda p: received.append(("interaction.resolved", p))
    )
    client.on_event(
        "interaction.requested", lambda p: received.append(("interaction.requested", p))
    )
    _consume_wire_events(client, _real_wire_stream(kernel_events))

    resolved = [p for n, p in received if n == "interaction.resolved"]
    requested = [p for n, p in received if n == "interaction.requested"]
    assert requested and len(requested) == 2, (
        f"应有两次 requested（apr-A/i-B）: {[(n, p.get('approvalId') or p.get('interactionId')) for n, p in received]}"
    )
    assert resolved, "INPUT_REQUIRED 的 resolved 帧被吞,未派发 interaction.resolved"
    assert resolved[0].get("interactionId") == "apr-A", resolved[0]
    # 部分决议帧绝不允许再派发第三次 requested
    assert len(requested) == 2, (
        "已决议帧被误派发为新 interaction.requested"
        f"（第 3 次）: {[(n, p.get('approvalId') or p.get('interactionId')) for n, p in received]}"
    )


def test_chat_abort_reports_unsupported_not_fake_success(stream_server):
    """老引擎回归：card 不声明 cancel 能力（1c 之前/灰度期残留）——本地断流
    ≠Engine 取消，不得报告 ok=True（R18-2 语义保留为能力门分支）。"""

    async def scenario():
        client = await _connect_async(stream_server.base)
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(0.1)  # 流开跑
        out = await client.chat_abort(session_key="s")
        await client.close()
        return out

    out = _run(scenario())
    # 引擎未声明取消能力——诚实面就是不支持（无 CancelTask 盲发）
    assert out["ok"] is False, f"abort 伪成功: {out}"
    assert out["error"]["code"] == "CANCEL_UNSUPPORTED"


def test_resolve_malformed_response_matrix():
    """/[]、error:null、result+error 并存三例都不得 accepted=True
    （R18-3 复现：当前前例还抛 AttributeError）。"""
    cases = [
        lambda: JSONResponse([]),  # 非对象
        lambda: {"id": "res-i", "error": None},  # 双无
        lambda: {"id": "res-i", "result": {}, "error": {}},  # 双有
    ]
    for responder in cases:
        srv = LiveServer(_resolve_app_with(responder))
        try:

            async def scenario():
                client = await _connect_async(srv.base)
                return await client.interaction_resolve(
                    interaction_id="i",
                    resolution=InteractionResolution(decision="approve"),
                )

            out = _run(scenario())  # 不得抛异常
            assert out.accepted is False, f"畸形响应被判成功: {out.response}"
            assert isinstance(out, EngineInteractionResolveExchange)
        finally:
            srv.stop()
