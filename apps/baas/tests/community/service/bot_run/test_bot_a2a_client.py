"""BotA2aClient 契约测试——R17 修正版（真实 TCP 流式 fixture）。

Fixture 是**真 uvicorn 服务器**（非 ASGITransport——它的响应生成器先完成
才整块返回，无法验证流式时序——R16-9 的实证结论）。每测试起独立
epenthread 端口，断言基于真实 socket 分块到达时序。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from secbaas.community.api.bot_interaction._models import InteractionResolution
from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError,
    BotA2aClient,
)
from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionResolveExchange,
)


def _sse(result: dict) -> str:
    return f"data: {json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result})}\n\n"


def make_stream_app(final_delay: float = 0.5) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {
            "supportedInterfaces": [
                {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
            ],
            "version": "0.1.0",
        }

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        if body["method"] == "SendStreamingMessage":
            session = body["params"]["message"]["contextId"]

            async def gen():
                yield _sse(
                    {
                        "statusUpdate": {
                            "taskId": "t1",
                            "contextId": session,
                            "status": {"state": "TASK_STATE_WORKING"},
                            "metadata": {
                                "kind": "agent",
                                "seq": 1,
                                "original_event": "agent",
                                "engine_frame": {
                                    "stream": "tool",
                                    "data": {"phase": "start"},
                                    "state": "",
                                },
                            },
                        }
                    }
                )
                yield _sse(
                    {
                        "statusUpdate": {
                            "taskId": "t1",
                            "contextId": session,
                            "status": {"state": "TASK_STATE_WORKING"},
                            "metadata": {
                                "kind": "agent",
                                "original_event": "mode_transition.resolved",
                                "seq": 2,
                                "engine_frame": {"state": "resolved", "mode": "plan"},
                            },
                        }
                    }
                )
                yield _sse(
                    {
                        "artifactUpdate": {
                            "taskId": "t1",
                            "contextId": session,
                            "artifact": {"parts": [{"text": "你好"}]},
                            "append": False,
                            "lastChunk": False,
                            "metadata": {"seq": 3},
                        }
                    }
                )
                await asyncio.sleep(final_delay)  # 真流式：delta→final 窗口
                yield _sse(
                    {
                        "artifactUpdate": {
                            "taskId": "t1",
                            "contextId": session,
                            "artifact": {"parts": [{"text": "你好，完整终稿"}]},
                            "append": False,
                            "lastChunk": True,
                            "metadata": {"seq": 4},
                        }
                    }
                )
                yield _sse(
                    {
                        "statusUpdate": {
                            "taskId": "t1",
                            "contextId": session,
                            "status": {"state": "TASK_STATE_COMPLETED"},
                            "metadata": {"final": "true", "lastSeq": 5},
                        }
                    }
                )

            return StreamingResponse(gen(), media_type="text/event-stream")
        if body["method"] == "InjectContext":
            return {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"inject": "accepted"},
            }
        if body["method"] == "ResolveInteraction":
            err_conflict = body["params"]["resolution"].get("decision") == "deny"
            if err_conflict:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "error": {
                            "code": -32003,
                            "data": {"reason": "resolve_conflict"},
                        },
                    }
                )
            return {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"interaction": {"resolved": True}},
            }
        return JSONResponse(
            {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601}}
        )

    return app


def make_failed_app() -> FastAPI:
    """独立 FAILED 脚本——首个终态即 FAILED（R17-1：不追加于 COMPLETED 后）。"""
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
        body = await request.json()
        session = body["params"]["message"]["contextId"]

        async def gen():
            yield _sse(
                {
                    "artifactUpdate": {
                        "taskId": "t1",
                        "contextId": session,
                        "artifact": {"parts": [{"text": "将失败"}]},
                        "append": False,
                        "lastChunk": True,
                        "metadata": {"seq": 1},
                    }
                }
            )
            await asyncio.sleep(0.2)
            yield _sse(
                {
                    "statusUpdate": {
                        "taskId": "t1",
                        "contextId": session,
                        "status": {"state": "TASK_STATE_FAILED"},
                        "metadata": {
                            "final": "true",
                            "engine_frame": {"errorMessage": "engine broke"},
                            "lastSeq": 2,
                        },
                    }
                }
            )

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def make_gate_app() -> FastAPI:
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
        body = await request.json()
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {
                    "code": -32000,
                    "message": "auth_required",
                    "data": {"reason": "auth_required"},
                },
            }
        )

    return app


def make_card_app(binding: str, version: str) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {
            "supportedInterfaces": [
                {"protocolBinding": binding, "protocolVersion": version}
            ]
        }

    return app


class LiveServer:
    """真 uvicorn TCP 服务器（ephemeral 端口）——流式时序可信。"""

    def __init__(self, app: FastAPI):
        cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical")
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self._server.started:
            time.sleep(0.05)
        assert self._server.started
        self.port = self._server.servers[0].sockets[0].getsockname()[1]

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture()
def stream_server():
    srv = LiveServer(make_stream_app())
    yield srv
    srv.stop()


@pytest.fixture()
def failed_server():
    srv = LiveServer(make_failed_app())
    yield srv
    srv.stop()


@pytest.fixture()
def gate_server():
    srv = LiveServer(make_gate_app())
    yield srv
    srv.stop()


def _connect(base: str) -> BotA2aClient:
    return asyncio.run(_connect_async(base))


async def _connect_async(base: str) -> BotA2aClient:
    client = BotA2aClient(base_url=base)
    hello = await client.connect()
    assert hello["protocol"] == 1 and hello["a2a"] is True
    return client


def _run(coro, timeout: float = 15.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


# ── L1: ack 在 final 前返回（真流式 TCP）──


def test_ack_returns_before_final(stream_server):
    async def scenario():
        client = await _connect_async(stream_server.base)
        t0 = time.monotonic()
        ack = await client.chat_send(session_key="s", message="hi")
        elapsed = time.monotonic() - t0
        assert ack["ok"] is True and ack["a2a"] is True
        # 真流式 fixture 的 final_delay=0.5s——ack 必须显著早于
        assert elapsed < 0.4, f"ack blocked {elapsed:.2f}s (mock final at 0.5s)"
        await asyncio.sleep(1.0)  # body task 消费完
        await client.close()

    _run(scenario())


def test_first_delta_before_final_with_barrier(stream_server):
    """R17-1: 事件屏障——delta 到达时 final 必须尚未出现（真流式专属断言）。"""
    received: list[tuple[str, dict]] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event("chat", lambda p, _n="chat": received.append((_n, p)))
        client.on_event("agent", lambda p, _n="agent": received.append((_n, p)))
        client.on_event(
            "mode_transition.resolved",
            lambda p, _n="mode_transition.resolved": received.append((_n, p)),
        )  # R17-2 路由验证
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(0.25)  # final_delay=0.5s 之内
        states = {p.get("state") for _, p in received}
        return states

    states = _run(scenario())
    assert "delta" in states, f"delta missing at barrier: {states}"
    assert "final" not in states, f"final leaked before barrier: {states}"


def test_mode_transition_dispatches_to_original_event_name(stream_server):
    mt: list[dict] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event("mode_transition.resolved", lambda p: mt.append(p))
        client.on_event("agent", lambda p: mt.append({"__agent__": p}))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(1.0)
        await client.close()

    _run(scenario())
    assert any(p.get("mode") == "plan" for p in mt), (
        f"mode_transition.resolved 未按原事件名路由: {mt}"
    )


# ── T1: 终态单次 ──


def test_double_final_dedup_and_content_survived(stream_server):
    finals: list[dict] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event(
            "chat", lambda p: finals.append(p) if p.get("state") == "final" else None
        )
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(1.0)
        await client.close()

    _run(scenario())
    assert len(finals) == 1, f"terminal emitted {len(finals)}x"
    text = finals[0]["message"]["content"][0]["text"]
    assert text == "你好，完整终稿"


def test_failed_is_error_not_final(failed_server):
    seen: list[tuple[str, dict]] = []

    async def scenario():
        client = await _connect_async(failed_server.base)
        client.on_event("chat", lambda p: seen.append(("chat", p)))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(1.0)
        await client.close()

    _run(scenario())
    errors = [p for _, p in seen if p.get("state") == "error"]
    finals = [p for _, p in seen if p.get("state") == "final"]
    assert errors and not finals, (
        f"FAILED must be error, not final; seen={[p['state'] for _, p in seen]}"
    )
    assert "engine broke" in errors[0].get("errorMessage", "")


# ── E1: gate 拒绝与流错误 ──


def test_gate_rejection_not_ok(gate_server):
    async def scenario():
        client = await _connect_async(gate_server.base)
        return await client.chat_send(session_key="s", message="hi")

    out = _run(scenario())
    assert out["ok"] is False
    assert out["error"]["code"] == "auth_required"


# ── hitl: 真实 dataclass + exchange ──


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


# ── G1: 能力矩阵 ──


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


# ── attachments 防御（B3 编排层重路由之前的硬错）──


def test_attachments_rejected_without_silence(stream_server):
    async def scenario():
        client = await _connect_async(stream_server.base)
        return await client.chat_send(
            session_key="s", message="hi", attachments=[{"type": "image"}]
        )

    out = _run(scenario())
    assert out["ok"] is False
    assert out["error"]["code"] == "ATTACHMENTS_UNSUPPORTED"


# ── R17-7 metadata 平铺 + timeout（wire 断言在 server 端 pin）──


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


# ══════════ R18 复现测试（先红后修）══════════

# ── R18-1: interaction/approval 帧 lossless 往返 ──


def _real_wire_stream(kernel_events: list[dict]) -> list[dict]:
    """kernel 事件列表 → SSE wire 形（engine server._wire 的等价转换）——
    测试用它走真实内核序列化路径再喂 B1 消费器（R18-1 定位出的形式差异）。
    """
    from engine_shim import kernel_event_to_wire

    return [kernel_event_to_wire(e) for e in kernel_events]


async def _consume_wire_events_(
    client, wire_events: list[dict], rid: str = "rid"
) -> None:
    import secbaas.community.core.service.bot_run._bot_a2a_client as m

    for wire_event in wire_events:
        await client._handle_wire_result(m._RunCtx(rid, "s"), wire_event)


def _consume_wire_events(client, wire_events: list[dict], rid: str = "rid") -> None:
    import asyncio

    asyncio.run(_consume_wire_events_(client, wire_events, rid))


def test_interaction_requested_roundtrip_lossless():
    """R18-1 复现的测试(先红)：kernel→wire→B1 真往返——
    questions/kind/allowedDecisions 不得丢失。"""
    import asyncio

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
    import asyncio

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


# ── R18-2: abort 不伪成功 ──


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


# ── R18-3: resolve 畸形响应三例 ──


def _resolve_app_with(raw_responder):
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
        return raw_responder()

    return app


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


# ══════════ 1c: chat.abort → CancelTask 转发（TC-CANCEL）══════════

CANCEL_EXT_URI = "https://avernet.local/a2a/ext/cancel/v1"


def _cancel_capable_card() -> dict:
    """能力卡声明 cancel extension 的引擎形态（1c 起的真引擎）。"""
    return {
        "supportedInterfaces": [
            {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "version": "0.1.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extensions": [{"url": CANCEL_EXT_URI, "required": False}],
        },
    }


def make_cancel_app(*, mode: str = "cancels") -> tuple[FastAPI, dict]:
    """1c CancelTask 前后端速写（真 TCP）。

    mode:
      cancels  — 流开跑后等 cancel 信号，收到即推 CANCELED 终态经流收尾
      refuse   — CancelTask 出 task_terminal 标准错误；流自行 COMPLETED
      http_500 — CancelTask 通道回 500；流自行 COMPLETED

    返回 (app, handle)：handle["bodies"] 记录 server 收到的 CancelTask 请求。
    """
    app = FastAPI()
    state: dict[str, Any] = {"bodies": [], "cancel_seen": threading.Event()}

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return _cancel_capable_card()

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        method = body.get("method")
        if method == "SendStreamingMessage":
            session = body["params"]["message"]["contextId"]

            async def gen():
                yield _sse(
                    {
                        "statusUpdate": {
                            "taskId": "eng-1",
                            "contextId": session,
                            "status": {"state": "TASK_STATE_WORKING"},
                            "metadata": {"seq": 1},
                        }
                    }
                )
                yield _sse(
                    {
                        "artifactUpdate": {
                            "taskId": "eng-1",
                            "contextId": session,
                            "artifact": {"parts": [{"text": "回答中"}]},
                            "append": False,
                            "lastChunk": False,
                            "metadata": {"seq": 2},
                        }
                    }
                )
                if mode == "cancels":
                    # 等 cancel 信号——分片轮询，绝不阻塞 server loop
                    # （否则 CancelTask 请求本身进不来，同 loop 死锁）。
                    for _ in range(200):
                        if state["cancel_seen"].is_set():
                            break
                        await asyncio.sleep(0.05)
                    yield _sse(
                        {
                            "statusUpdate": {
                                "taskId": "eng-1",
                                "contextId": session,
                                "status": {"state": "TASK_STATE_CANCELED"},
                                "metadata": {"final": "true", "lastSeq": 3},
                            }
                        }
                    )
                else:
                    await asyncio.sleep(0.5)
                    yield _sse(
                        {
                            "artifactUpdate": {
                                "taskId": "eng-1",
                                "contextId": session,
                                "artifact": {"parts": [{"text": "完整终稿"}]},
                                "append": False,
                                "lastChunk": True,
                                "metadata": {"seq": 3},
                            }
                        }
                    )
                    yield _sse(
                        {
                            "statusUpdate": {
                                "taskId": "eng-1",
                                "contextId": session,
                                "status": {"state": "TASK_STATE_COMPLETED"},
                                "metadata": {"final": "true", "lastSeq": 4},
                            }
                        }
                    )

            return StreamingResponse(gen(), media_type="text/event-stream")
        if method == "CancelTask":
            state["bodies"].append(body)
            if mode == "cancels":
                state["cancel_seen"].set()
                return {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "task": {
                            "id": body["params"]["taskId"],
                            "status": {"state": "TASK_STATE_CANCELED"},
                        }
                    },
                }
            if mode == "refuse":
                # 终态后取消 → 标准错误（task_terminal，data 带 state 判读）
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "error": {
                            "code": -32000,
                            "message": "task already terminal (TASK_STATE_COMPLETED)",
                            "data": {
                                "reason": "task_terminal",
                                "state": "TASK_STATE_COMPLETED",
                                "already_terminal": True,
                            },
                        },
                    }
                )
            if mode == "malformed":
                # 200 但无 result 也无 error（id 也不回显）——畸形响应
                return {"wrong": "shape", "id": None}
            return JSONResponse({"boom": True}, status_code=500)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601}}
        )

    return app, state


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
        cancels = [b for b in state["bodies"] if b.get("method") == "CancelTask"]
        assert len(cancels) == 1, f"CancelTask 上 wire {len(cancels)} 次"
        assert cancels[0]["params"]["taskId"] == "eng-1", cancels[0]
        assert cancels[0]["jsonrpc"] == "2.0"
        errors = [p for p in seen if p.get("state") == "error"]
        assert errors, f"no terminal to consumer: {seen}"
        assert errors[0].get("errorCode") == "TASK_STATE_CANCELED"
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
        acc = AsyncChatClient(uri="ws://unused")
        acc._transport = "a2a"
        acc._client = _FakeTransport("a2a")
        acc._v3_fallback = _FakeTransport("v3")
        acc._v3_routed_sessions.add("s-att")
        await acc.chat_abort("s-att", "r1")  # B3 reroute 过的 run
        await acc.chat_abort("s-normal")  # A2A 正常 session

        acc2 = AsyncChatClient(uri="ws://unused")
        acc2._transport = "v3-ws"  # off 模式
        acc2._client = _FakeTransport("v3-ws")
        acc2._v3_routed_sessions.add("s-att")  # 脏标记也不得干扰 v3
        await acc2.chat_abort("s-att")

    _run(scenario())
    assert calls == [
        ("v3", "s-att", "r1"),
        ("a2a", "s-normal", None),
        ("v3-ws", "s-att", None),
    ]
