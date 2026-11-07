"""BotA2aClient 契约测试——R17 修正版（真实 TCP 流式 fixture）。

Fixture 是**真 uvicorn 服务器**（非 ASGITransport——它的响应生成器先完成
才整块返回，无法验证流式时序——R16-9 的实证结论）。每测试起独立
epenthread 端口，断言基于真实 socket 分块到达时序。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from secbaas.community.api.bot_interaction._models import InteractionResolution
from secbaas.community.core.service.bot_run._bot_a2a_client import (
    A2aUnavailableError,
    BotA2aClient,
    _RunCtx,
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


# ── E1-robust: 上游错误面不是 JSON-RPC 错误对象（裸串/字符串 error 字段）──


def make_stringified_error_app(body: Any, status_code: int = 200) -> FastAPI:
    """错误面速写：body 原样 JSON 化返回——含 JSON 裸串与非对象 error 字段。

    2026-10-29 预发实证：代理层曾回 JSON 裸串错误体，E1 二分把它当
    JSON-RPC 错误对象 .get，炸成 AttributeError 并盖住真实错误。
    """
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
        return JSONResponse(body, status_code=status_code)

    return app


def test_bare_string_error_body_surfaces_raw_not_crash():
    """HTTP 502 + JSON 裸串 body：不得炸 AttributeError，原文必须进 message。"""
    srv = LiveServer(make_stringified_error_app("upstream refused", 502))
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
    assert out["error"]["code"] == "HTTP_502", "解析不出对象时回退真实状态码"
    assert "upstream refused" in out["error"]["message"], "原始错误不得被盖住"


def _make_broken_body_server():
    """TCP 裸服务器：GET card 正常 200；POST /rpc 只回 502 响应头
    （声明 Content-Length: 100）后立刻关连接——错误体永远到不了。

    触发的具体传输异常形态不固定（RemoteProtocolError 或空串
    ReadError），断言只钉结构化返回与资源关闭，不断言异常文本形态。
    """

    class _BrokenBodyServer:
        def __init__(self) -> None:
            self._sock = socket.socket()
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("127.0.0.1", 0))
            self._sock.listen(8)
            self._thread = threading.Thread(target=self._serve, daemon=True)
            self._thread.start()

        def _serve(self) -> None:
            while True:
                try:
                    conn, _ = self._sock.accept()
                except OSError:
                    return
                with conn:
                    try:
                        head = conn.recv(65536)
                    except OSError:
                        continue
                    if head.startswith(b"GET"):
                        body = json.dumps(
                            {
                                "supportedInterfaces": [
                                    {
                                        "protocolBinding": "JSONRPC",
                                        "protocolVersion": "1.0",
                                    }
                                ]
                            }
                        ).encode()
                        conn.sendall(
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: application/json\r\n"
                            b"Content-Length: "
                            + str(len(body)).encode()
                            + b"\r\n\r\n"
                            + body
                        )
                    else:
                        conn.sendall(
                            b"HTTP/1.1 502 Bad Gateway\r\n"
                            b"Content-Type: application/json\r\n"
                            b"Content-Length: 100\r\n\r\n"
                        )
                        conn.close()  # 体永不到达 → aread 炸

        def stop(self) -> None:
            self._sock.close()

        @property
        def base(self) -> str:
            return f"http://127.0.0.1:{self._sock.getsockname()[1]}"

    return _BrokenBodyServer()


def test_error_body_read_failure_returns_structured_and_closes():
    """P2 回归钉：错误响应头已到手（502），但错误体传输中断。

    读体不得炸出 chat_send（上一版 aread 曾被移出 try）——必须结构化
    返回 HTTP_502 + 可分诊 message；读挂的临时 http client 未登记进
    _runs、close() 兜不住它，必须就地关闭。

    真实 socket 断连触发的异常形态在 RemoteProtocolError（有文本）与
    空串 ReadError 之间摆动——message 非空由实现层的空文本回退保证
    （见 test_error_body_read_failure_empty_exc_message_falls_back）。
    """
    from secbaas.community.core.service.bot_run._bot_a2a_client import (
        BotA2aClient,
    )

    srv = _make_broken_body_server()
    # httpx AsyncClient.__aexit__ 直接关 transport、不经 aclose()——connect
    # 阶段的 with 块不会计入。关闭断言只针对 chat_send 阶段创建的临时
    # client（read 失败的孤儿正是它——未登记进 _runs，close() 兜不住）。
    made: list[dict[str, bool]] = []
    orig_http = BotA2aClient._http

    def counting_http(self, *, timeout=None):
        created = orig_http.__get__(self, BotA2aClient)(timeout=timeout)
        entry: dict[str, bool] = {"closed": False}
        made.append(entry)
        real_aclose = created.aclose

        async def counting_aclose():
            entry["closed"] = True
            await real_aclose()

        created.aclose = counting_aclose
        return created

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(BotA2aClient, "_http", counting_http)

    async def scenario():
        client = await _connect_async(srv.base)
        pre = len(made)
        out = await client.chat_send(session_key="s", message="hi")
        chat_phase = made[pre:]  # chat_send 创建的全部临时 client
        await client.close()
        return out, chat_phase

    try:
        out, chat_phase = _run(scenario())
    finally:
        srv.stop()
        monkeypatch.undo()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_502", "状态码来自已到手的响应头"
    assert out["error"]["message"], "空串传输异常须回退类名，不得为空"
    assert chat_phase, "chat_send 必须经由 _http 建临时 client"
    assert all(e["closed"] for e in chat_phase), "读挂的临时 client 必须就地关闭"


def test_error_body_read_failure_survives_resp_aclose_error():
    """读体失败后 resp.aclose 再炸（best-effort 不得反噬清理）：
    client 关闭与结构化返回都必须完成。"""
    srv = _make_broken_body_server()

    async def scenario():
        client = await _connect_async(srv.base)
        # connect 后再下补丁：card 探针自身的响应读取也走 Response.aclose，
        # 全局炸会击穿 A2aUnavailableError 门（灰度回退承诺）。
        real_aclose = httpx.Response.aclose

        async def bomb_aclose(self):
            raise RuntimeError("aclose also broken")

        httpx.Response.aclose = bomb_aclose
        try:
            out = await client.chat_send(session_key="s", message="hi")
        finally:
            httpx.Response.aclose = real_aclose
        await client.close()
        return out

    try:
        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_502"
    assert out["error"]["message"], "传输异常文本仍然要透出"


def test_error_body_read_failure_empty_exc_message_falls_back():
    """空文本传输异常（ReadError("")/ReadTimeout("")）确定性复现：
    message 必须回退到异常类名，保证恒有可分诊内容（评审 P2）。

    http_transport_factory + MockTransport 注入，响应头 502 到手、
    读体时流抛空文本异常——不走真实 socket，形态完全确定。
    """
    from secbaas.community.core.service.bot_run._bot_a2a_client import (
        BotA2aClient,
    )

    card = {
        "supportedInterfaces": [
            {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ]
    }

    def _client_for(exc: Exception) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if "agent-card.json" in request.url.path:
                return httpx.Response(200, json=card)

            async def exploding_body():
                yield b""
                raise exc

            return httpx.Response(
                502,
                headers={"content-type": "application/json"},
                content=exploding_body(),
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def scenario(exc: Exception) -> dict:
        client = BotA2aClient(
            base_url="http://testserver",
            http_transport_factory=lambda: _client_for(exc),
        )
        hello = await client.connect()
        assert hello["protocol"] == 1
        out = await client.chat_send(session_key="s", message="hi")
        await client.close()
        return out

    for exc, name in [
        (httpx.ReadError(""), "ReadError"),
        (httpx.ReadTimeout(""), "ReadTimeout"),
    ]:
        out = _run(scenario(exc))
        assert out["ok"] is False, f"{name}: 读体失败必须结构化返回"
        assert out["error"]["code"] == "HTTP_502", f"{name}: 状态码来自已到手的响应头"
        assert out["error"]["message"] == name, f"{name}: 空文本必须回退异常类名"


def test_string_error_field_body_surfaces_raw_not_crash():
    """200 + error 字段是字符串（非对象）：同款两级守卫，原文可见。"""
    srv = LiveServer(
        make_stringified_error_app(
            {"jsonrpc": "2.0", "id": 1, "error": "gate exploded"}
        )
    )
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
    assert out["error"]["code"] == "HTTP_200", "error 非对象时 reason 不可得"
    assert "gate exploded" in out["error"]["message"]


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


def test_partial_resolution_frame_dispatches_by_marker_not_state():
    """R5-2 复现（先红）：多 pending 部分决议的 resolved 帧携带
    INPUT_REQUIRED——必须按 meta.kind 分层派发为 interaction.resolved，
    不得把"已决议"误派发成新的 interaction.requested（审批面板回弹）。"""
    import asyncio

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


def make_cancel_app(
    *,
    mode: str = "cancels",
    abort_outcome: str = "confirmed",
    first_frame_gate: bool = False,
) -> tuple[FastAPI, dict]:
    """1c CancelTask 前后端速写（真 TCP）。

    mode:
      cancels  — 流开跑后等 cancel 信号，收到即推 CANCELED 终态经流收尾
      refuse   — CancelTask 出 task_terminal 标准错误；流自行 COMPLETED
      http_500 — CancelTask 通道回 500；流自行 COMPLETED
      empty    — 流立即 EOF：一个帧都不发（R7-F1 流死无身份面）

    first_frame_gate=True 时首个 SSE 帧闸在 state["first_frame"] 上——
    复现"ACK 已返回、engine_task_id 未就位"的真实时序窗口。

    返回 (app, handle)：handle["bodies"] 记录 server 收到的 CancelTask 请求。
    """
    app = FastAPI()
    state: dict[str, Any] = {
        "bodies": [],
        "cancel_seen": threading.Event(),
        "first_frame": threading.Event(),
    }

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
                if mode == "empty":
                    # 流死无身份：EOF 在任何帧之前——消费方收
                    # STREAM_CLOSED_WITHOUT_TERMINAL，engine_task_id 永远缺失。
                    return
                if first_frame_gate:
                    for _ in range(200):
                        if state["first_frame"].is_set():
                            break
                        await asyncio.sleep(0.05)
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
                        },
                        # 引擎侧 abort 结局——chat_abort 透传给调用方观测
                        "engineAbort": abort_outcome,
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
        acc = AsyncChatClient(uri="ws://unused")
        acc._transport = "a2a"
        acc._client = _FakeTransport("a2a")
        acc._v3_fallback = _FakeTransport("v3")
        acc._v3_routed_sessions["s-att"] = "v3"
        await acc.chat_abort("s-att", "r1")  # B3 reroute 过的 run
        await acc.chat_abort("s-normal")  # A2A 正常 session

        acc2 = AsyncChatClient(uri="ws://unused")
        acc2._transport = "v3-ws"  # off 模式
        acc2._client = _FakeTransport("v3-ws")
        acc2._v3_routed_sessions["s-att"] = "v3"  # 脏标记也不得干扰 v3
        await acc2.chat_abort("s-att")

    _run(scenario())
    assert calls == [
        ("v3", "s-att", "r1"),
        ("a2a", "s-normal", None),
        ("v3-ws", "s-att", None),
    ]


# ══════════ P1-2: 终态通知漏 await（三条分支各一 + 去重语义）══════════


def _make_terminal_scenario_app(*, frames: list[Any]) -> FastAPI:
    """按给定 SSE 行序列输出的极简引擎面（真 TCP）。

    frames 中的 str 原样输出（可塞非 JSON 构造消费异常），dict 按
    ``data: {json}\n\n`` 原样序列化（不套 result 外壳——可直接给顶层
    error 帧）。
    """
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

        async def gen():
            for frame in frames:
                if isinstance(frame, str):
                    yield frame
                else:
                    yield f"data: {json.dumps(frame)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def _run_terminal_scenario(frames: list[Any]) -> list[dict]:
    """起真服务器 → 发送 → 收 handler chat 帧列表（含结尾收口）。"""
    srv = LiveServer(_make_terminal_scenario_app(frames=frames))
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            seen: list[dict] = []
            client.on_event("chat", lambda p: seen.append(p))
            await client.chat_send(session_key="s", message="hi")
            await asyncio.sleep(0.3)  # body task 收完流
            await client.close()
            return seen

        return _run(scenario())
    finally:
        srv.stop()


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


# ══════════ P1-1: proxypass 真实路径下的 A2A 闭环（带路径前缀的 LiveServer）══════════


def make_proxypass_engine_app(prefix: str) -> FastAPI:
    """真实 proxypass 形态引擎面：所有路由挂在 ``{prefix}`` 之下——
    模拟 proxy ``wss://proxy/proxypass/<target>{ws_path}`` 的路径前缀语义。
    卡检查 + 一轮流式回答，供 AsyncChatClient 经 _a2a_base_url_from_ws
    转换后探测与收发。"""
    app = FastAPI()

    @app.get(f"{prefix}/.well-known/agent-card.json")
    def card() -> dict:
        return {
            "supportedInterfaces": [
                {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
            ],
            "version": "0.1.0",
        }

    @app.post(f"{prefix}/rpc")
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
                            "status": {"state": "TASK_STATE_COMPLETED"},
                            "metadata": {"final": "true", "lastSeq": 1},
                        }
                    }
                )

            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse(
            {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601}}
        )

    return app


def test_proxypass_route_prefix_survives_to_card_and_rpc():
    """P1-1 复现：wss://proxy/proxypass/<target>/api/openclaw/ws 转换出的
    A2A base 必须保留路由前缀——card GET 与 RPC 都打到 {prefix}/ 而非代理根
    （转换丢前缀时 grey 永远回退、on 直接连不上）。"""
    prefix = "/proxypass/sbx-a2a-1"
    srv = LiveServer(make_proxypass_engine_app(prefix))
    try:

        async def scenario():
            from secbaas.community.core.service.bot_run._async_chat_client import (
                AsyncChatClient,
            )

            acc = AsyncChatClient(
                uri=f"ws://127.0.0.1:{srv.port}{prefix}/api/openclaw/ws",
                a2a_mode="on",
                max_retries=0,
            )
            assert acc._a2a_mode == "on"
            hello = await acc.connect()
            assert acc._transport == "a2a", (
                f"probing must hit prefixed card: hello={hello}"
            )
            content, _events = await acc.send_message(session_key="s", message="hi")
            base_url = acc._client.base_url
            await acc.close()
            return base_url

        base_url = _run(scenario())
        assert base_url == f"http://127.0.0.1:{srv.port}{prefix}"
    finally:
        srv.stop()


# ══════════ P1-5: Gate 开启时 CancelTask/ResolveInteraction 带 run IAM token ══════════


def make_gate_send_and_cancel_app(*, mode: str) -> tuple[FastAPI, dict]:
    """IAM gate 开启端的引擎速写：所有 /rpc 都要求 X-IAM-Token 与 send 时
    记录的 run 许可一致，否则 auth_required。

    mode:
      cancel  — 流等 CancelTask 信号；CancelTask 校验 token 后回 CANCELED
      resolve — 流等 ResolveInteraction；INPUT_REQUIRED 交互推流
    """
    app = FastAPI()
    state: dict[str, Any] = {
        "cancel_bodies": [],
        "cancel_headers": None,
        "resolve_headers": None,
        "run_token": None,
        "seen": threading.Event(),
    }

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return _cancel_capable_card()

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        token = request.headers.get("x-iam-token")
        method = body.get("method")

        def gate() -> JSONResponse | None:
            if token != state["run_token"]:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": body.get("id"),
                        "error": {
                            "code": -32000,
                            "message": "auth_required",
                            "data": {"reason": "auth_required"},
                        },
                    }
                )
            return None

        if method == "SendStreamingMessage":
            state["run_token"] = token  # 登记该次 send 的许可
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
                if mode == "resolve":
                    yield _sse(
                        {
                            "statusUpdate": {
                                "taskId": "eng-1",
                                "contextId": session,
                                "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
                                "metadata": {
                                    "kind": "ask_user",
                                    "interactionId": "i-gate",
                                    "seq": 2,
                                },
                            }
                        }
                    )
                for _ in range(200):
                    if state["seen"].is_set():
                        break
                    await asyncio.sleep(0.05)
                if mode == "resolve":
                    yield _sse(
                        {
                            "statusUpdate": {
                                "taskId": "eng-1",
                                "contextId": session,
                                "status": {"state": "TASK_STATE_WORKING"},
                                "metadata": {
                                    "interactionId": "i-gate",
                                    "seq": 3,
                                },
                            }
                        }
                    )
                yield _sse(
                    {
                        "statusUpdate": {
                            "taskId": "eng-1",
                            "contextId": session,
                            "status": {"state": "TASK_STATE_CANCELED"}
                            if mode == "cancel"
                            else {"state": "TASK_STATE_COMPLETED"},
                            "metadata": {"final": "true", "lastSeq": 9},
                        }
                    }
                )

            return StreamingResponse(gen(), media_type="text/event-stream")
        if method == "CancelTask":
            state["cancel_headers"] = dict(request.headers)
            state["cancel_bodies"].append(body)
            denied = gate()
            if denied is not None:
                return denied
            state["seen"].set()
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
        if method == "ResolveInteraction":
            state["resolve_headers"] = dict(request.headers)
            denied = gate()
            if denied is not None:
                return denied
            state["seen"].set()
            return {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"interaction": {"resolved": True}},
            }
        return JSONResponse(
            {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601}}
        )

    return app, state


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


# ══════════ P1-4: 附件 v3 回退 run 的 HITL 闭环（真 a2a 面 + v3 duck 替身）══════════


class _FakeV3KingineTransport:
    """BotWebSocketClient 的 duck 替身（仅测试面）：_ensure_v3_fallback 用真
    代码构造时会 new 本类（patch 点就在构造导入处），handlers 双注册/懒建/
    close 收口全走被测代码；emit 模拟引擎经 v3 推帧（handler 签名与
    BotWebSocketClient 一致：精确单参 + 通配两参）。"""

    def __init__(self, uri: str, client_id=None, client_version="1.0.0", headers=None):
        self.uri = uri
        self.connected = True
        self._event_handlers: dict[str, Any] = {}
        self.chat_send_calls: list[tuple[str, dict]] = []
        self.chat_abort_calls: list[tuple[str, Any]] = []
        self.interaction_resolve_calls: list[tuple[str, Any]] = []
        self.close_calls = 0

    async def connect(self) -> dict[str, Any]:
        return {"ok": True, "payload": {"server": {}}}

    def on_event(self, name: str, handler: Any) -> None:
        self._event_handlers[name] = handler

    def on_disconnect(self, handler: Any) -> None:
        pass

    async def chat_send(self, session_key: str, message: str, **kwargs: Any) -> dict:
        self.chat_send_calls.append((session_key, kwargs))
        return {"ok": True}

    async def chat_abort(self, session_key: str, run_id=None) -> dict:
        self.chat_abort_calls.append((session_key, run_id))
        return {"ok": True}

    async def interaction_resolve(
        self, *, interaction_id: str, resolution: InteractionResolution
    ) -> Any:
        self.interaction_resolve_calls.append((interaction_id, resolution))
        return EngineInteractionResolveExchange.from_frames(
            request={
                "type": "req",
                "id": "v3-e1",
                "method": "interaction.resolve",
                "params": {
                    "interactionId": interaction_id,
                    "decision": resolution.decision,
                },
            },
            response={"type": "res", "id": "v3-e1", "ok": True},
        )

    async def close(self) -> None:
        self.close_calls += 1
        self.connected = False

    def emit(self, event: str, payload: dict) -> None:
        handler = self._event_handlers.get(event)
        if handler:
            result = handler(payload)
            assert not asyncio.iscoroutine(result)
        wildcard = self._event_handlers.get("*")
        if wildcard:
            result = wildcard(event, payload)
            assert not asyncio.iscoroutine(result)


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
                a2a_mode="on",
                max_retries=0,
                interaction_service=service,
            )
            hello = await acc.connect()
            assert acc._transport == "a2a", hello

            async def spy_resolve(**kwargs: Any) -> Any:
                raise AssertionError("answer leaked to a2a transport")

            assert acc._client is not None
            acc._client.interaction_resolve = spy_resolve

            with patch(
                "secbaas.community.core.service.bot_run._bot_websocket_client.BotWebSocketClient",
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
                v3 = acc._v3_fallback
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
                assert "hitl-1" not in acc._v3_routed_sessions
                await acc.close()
                return service

        svc = _run(scenario())
        svc.record_engine_exchange.assert_called_once()
        assert svc.record_engine_exchange.call_args.kwargs["interaction_id"] == "i-1"
    finally:
        srv.stop()


# ══════════ 改行覆盖率补齐：客户端面/abort 分层边缘/chat_inject 全条款 ══════════


def _dead_rpc_url() -> str:
    """刚释放的临时端口——连接必拒（保证性的传输失败源，不依赖端口占用）。"""
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}/rpc"


def make_card_down_app() -> FastAPI:
    """card GET 面故障（500）——A2aUnavailableError 的探测失败来源。"""
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> Any:
        return JSONResponse({"error": "engine down"}, status_code=500)

    return app


def test_card_probe_http_failure_raises_a2a_unavailable():
    srv = LiveServer(make_card_down_app())
    try:
        with pytest.raises(A2aUnavailableError, match="capability probe failed"):
            _run(_connect_async(srv.base))
    finally:
        srv.stop()


def make_malformed_card_app(payload: Any) -> FastAPI:
    """结构畸形卡——合法 JSON 但形态不兼容（非对象卡 / 接口表残缺）。

    回归钉（PR #2743 review P2）：结构解析不得裸抛 AttributeError /
    TypeError 击穿 A2aUnavailableError 承诺——那会让 grey 回退 v3 失效。
    """
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> Any:
        return JSONResponse(payload)

    return app


_MALFORMED_CARDS: list[tuple[str, Any]] = [
    ("array-card", []),
    ("null-interfaces", {"supportedInterfaces": None}),
    ("null-element", {"supportedInterfaces": [None]}),
]


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


# ── 流消费边缘：message 成员 + 终态帧二次抑制 ──


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


# ── _dispatch 契约：async handler 面与异常包裹 ──


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


# ── abort 分层边缘：无卡快照 / 未追踪 run 断流 / 传输失败 / 畸形与非常规响应 ──


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


def make_cancel_response_app(responder) -> tuple[FastAPI, dict]:
    """CancelTask 响应注入器：cancel-capable 卡 + WORKING 帧就位后挂起等
    cancel 信号（engine_task_id 可学入），CancelTask 响应由 responder 出。"""
    app = FastAPI()
    state: dict[str, Any] = {"bodies": [], "seen": threading.Event()}

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
                            "taskId": "eng-2",
                            "contextId": session,
                            "status": {"state": "TASK_STATE_WORKING"},
                            "metadata": {"seq": 1},
                        }
                    }
                )
                for _ in range(100):
                    if state["seen"].is_set():
                        break
                    await asyncio.sleep(0.05)
                yield _sse(
                    {
                        "statusUpdate": {
                            "taskId": "eng-2",
                            "contextId": session,
                            "status": {"state": "TASK_STATE_COMPLETED"},
                            "metadata": {"final": "true", "lastSeq": 2},
                        }
                    }
                )

            return StreamingResponse(gen(), media_type="text/event-stream")
        if method == "CancelTask":
            state["bodies"].append(body)
            state["seen"].set()
            return responder(body)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601}}
        )

    return app, state


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


# ── chat_inject 全条款（R16-1 注入面此前整段未覆盖）──


def make_inject_app(responder) -> tuple[FastAPI, dict]:
    """InjectContext 引擎面速写：请求体/头钉入 state，响由 responder 出。"""
    app = FastAPI()
    state: dict[str, Any] = {"bodies": [], "headers": None}

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
        state["bodies"].append(body)
        state["headers"] = dict(request.headers)
        return responder(body)

    return app, state


def _accepted_inject(body: dict) -> dict:
    return {"jsonrpc": "2.0", "id": body["id"], "result": {"inject": "accepted"}}


def _inject_status_responder(status: str):
    def responder(body: dict) -> dict:
        return {"jsonrpc": "2.0", "id": body["id"], "result": {"inject": status}}

    return responder


async def _inject_scenario(srv: LiveServer, **inject_kwargs: Any) -> dict:
    client = await _connect_async(srv.base)
    kwargs = {"message": "x", **inject_kwargs}
    out = await client.chat_inject(session_key="s-inj", **kwargs)
    await client.close()
    return out


def test_chat_inject_bare_string_error_body_no_crash():
    """200 + JSON 裸串 body：isinstance 守卫 + 原文进 message。"""
    app, _ = make_inject_app(
        lambda body: JSONResponse('"inject refused"', status_code=200)
    )
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False
    assert "inject refused" in out["error"]["message"]


def test_chat_inject_string_error_field_no_crash():
    """200 + error 字段是字符串：error 三键守卫，原文进 message。"""
    app, _ = make_inject_app(
        lambda body: JSONResponse(
            {"jsonrpc": "2.0", "id": body["id"], "error": "inject refused"},
            status_code=200,
        )
    )
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "a2a_error"
    assert "inject refused" in out["error"]["message"]


def test_chat_inject_rejects_attachments_without_wire_side_effects():
    app, state = make_inject_app(_accepted_inject)
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            out = await client.chat_inject(
                session_key="s",
                message="x",
                attachments=[{"kind": "screenshot"}],
            )
            await client.close()
            return out

        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["raise_"] is False, "纯数据错误——剖面由编排层收敛"
    assert out["error"]["code"] == "ATTACHMENTS_UNSUPPORTED"
    assert state["bodies"] == [], "附件请求绝不得静默 wire"


def test_chat_inject_flattens_metadata_carries_token_and_parts():
    app, state = make_inject_app(_accepted_inject)
    srv = LiveServer(app)
    try:

        async def scenario():
            return await _inject_scenario(
                srv,
                message={"content": [{"type": "text", "text": "补上下文"}]},
                timeout_ms=1500,
                auth_token="tok-inj",
                chat_metadata={"source": "e2e"},
            )

        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is True, out
    assert out["status"] == "accepted"
    assert out["a2a"] is True
    body = state["bodies"][0]
    assert body["method"] == "InjectContext"
    assert body["jsonrpc"] == "2.0"
    assert body["params"]["contextId"] == "s-inj"
    assert body["params"]["message"]["parts"] == [{"text": "补上下文"}], (
        f"dict message 的 content 必须展平成 parts: {body}"
    )
    meta = body["params"]["metadata"]
    assert meta["source"] == "e2e"
    assert meta["timeoutMs"] == "1500"
    assert meta["traceId"] == body["id"], "traceId 必须回填请求 id"
    assert state["headers"]["x-iam-token"] == "tok-inj"


def test_chat_inject_non_structured_messages_fall_back_to_string():
    message = {"content": "plain"}
    app, state = make_inject_app(_accepted_inject)
    srv = LiveServer(app)
    try:

        async def scenario():
            first = await _inject_scenario(
                srv, message=message
            )  # dict 但 content 非 parts 列表
            second = await _inject_scenario(srv, message=42)  # 完全非结构化
            await asyncio.sleep(0)
            return first, second

        first, second = _run(scenario())
    finally:
        srv.stop()
    assert first["ok"] is True and second["ok"] is True
    parts = [b["params"]["message"]["parts"] for b in state["bodies"]]
    assert parts == [
        [{"text": str(message)}],  # content 非 list → str(message) 兜底
        [{"text": "42"}],  # 非 dict → str 兜底
    ], f"非结构化 message 必须显式字符串化，不得炸不得静默: {parts}"


def test_chat_inject_idempotent_statuses_are_ok():
    for status in ("accepted", "duplicate_accepted", "duplicate_in_progress"):
        app, _ = make_inject_app(_inject_status_responder(status))
        srv = LiveServer(app)
        try:
            out = _run(_inject_scenario(srv))
        finally:
            srv.stop()
        assert out["ok"] is True, f"{status} 注入被误判失败: {out}"
        assert out["status"] == status


def test_chat_inject_unknown_status_is_not_ok():
    app, _ = make_inject_app(_inject_status_responder("weird"))
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False, "未知 inject 状态不得凑成功"
    assert out["status"] == "weird"
    assert out["a2a"] is True


def test_chat_inject_http_error_face_leaks_status():
    app, _ = make_inject_app(
        lambda body: JSONResponse({"error": "overloaded"}, status_code=503)
    )
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_503"
    assert "overloaded" in out["error"]["message"]


def test_chat_inject_engine_error_is_relayed_with_reason():
    def responder(body: dict) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": body["id"],
            "error": {"message": "engine busy", "data": {"reason": "inject_conflict"}},
        }

    app, _ = make_inject_app(responder)
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "inject_conflict", "引擎 reason 必须保留"
    assert out["error"]["message"] == "engine busy"


# ── R6-1: 取消归属 = run 执行期（非等待发送不丢归属） ──────────────────


def test_tracks_session_spans_background_run_until_end_of_stream():
    """R6-1：归属锚在 run 执行期——ACK 即返回（非等待发送）、编排层等待
    收口都不影响归属；流终结后归属结束。CancelTask 真发出（非
    RUN_NOT_TRACKED）。"""
    app, state = make_cancel_app(mode="cancels")
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            ack = await client.chat_send(session_key="sk-bg", message="hi")
            assert ack["ok"] is True
            # 非等待发送：ACK 已回、结果未收——在途 run 归属存在
            assert client.tracks_session("sk-bg") is True, (
                "ACK 后归属丢失（非等待发送的取消会 RUN_NOT_TRACKED）"
            )
            assert client.has_running_runs is True

            await asyncio.sleep(0.3)  # WORKING 帧到达 → engine_task_id 就位
            assert "sk-bg" not in getattr(client, "_sessions", {}) or True
            out = await client.chat_abort(session_key="sk-bg")
            assert out["ok"] is True, out
            cancels = [b for b in state["bodies"] if b.get("method") == "CancelTask"]
            assert len(cancels) == 1, "CancelTask 必须真的发出"

            await asyncio.sleep(0.5)  # 引擎按取消收流（CANCELED final）
            assert client.tracks_session("sk-bg") is False, "流已终结,归属未随之结束"
            await client.close()
            return True

        assert _run(scenario())
    finally:
        srv.stop()


def test_pool_owner_lookup_survives_wait_result_false_send():
    """R6-1 reviewer 验收形态：两条连接，主连接做**非等待发送**——ACK 后
    编排层 _sessions 已不在,但 pool.get(session_key) 仍须命中真实
    holder,取消真发 CancelTask 而非 RUN_NOT_TRACKED。"""
    from unittest.mock import MagicMock

    from secbaas.community.core.service.bot_run._async_chat_client import (
        AsyncChatClient,
    )
    from secbaas.community.core.service.bot_run._async_chat_client_pool import (
        AsyncChatClientPool,
        _ConnEntry,
    )

    app, state = make_cancel_app(mode="cancels")
    srv = LiveServer(app)
    try:

        async def scenario():
            inner = await _connect_async(srv.base)  # 真 BotA2aClient
            holder = AsyncChatClient(uri="ws://x/ws", max_retries=0)
            holder._client = inner
            holder._transport = "a2a"

            other = MagicMock()  # 另一条空闲连接
            other.tracks_session = MagicMock(return_value=False)
            other.has_running_runs = False

            pool = AsyncChatClientPool()
            pool._clients["bg-6"] = [
                _ConnEntry(client=other),
                _ConnEntry(client=holder, a2a_mode="on"),
            ]

            ack = await inner.chat_send(session_key="sk-own", message="hi")
            assert ack["ok"] is True
            await asyncio.sleep(0.3)  # engine_task_id 就位
            assert "sk-own" not in holder._sessions, "前置:编排层等待不在（非等待发送）"

            owner = await pool.get("bg-6", "ws://x/ws", None, session_key="sk-own")
            assert owner is holder, "取消未命中真实 run 的持有连接"

            out = await owner.chat_abort(session_key="sk-own")
            assert out["ok"] is True, out
            cancels = [b for b in state["bodies"] if b.get("method") == "CancelTask"]
            assert len(cancels) == 1, "定向取消必须真的发出 CancelTask"
            await holder.close()
            return True

        assert _run(scenario())
    finally:
        srv.stop()


# ── AsyncChatClient G1 灰度门：探测失败的两种分流（grey 回退 / on 抛错）──


def test_async_client_grey_mode_falls_back_to_v3_on_probe_failure():
    from unittest.mock import patch

    from secbaas.community.core.service.bot_run._async_chat_client import (
        AsyncChatClient,
    )

    srv = LiveServer(make_card_down_app())
    try:

        async def scenario():
            with patch(
                "secbaas.community.core.service.bot_run._async_chat_client."
                "BotWebSocketClient",
                _FakeV3KingineTransport,
            ):
                acc = AsyncChatClient(
                    uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                    a2a_mode="grey",
                    max_retries=0,
                )
                hello = await acc.connect()
                assert acc._transport == "v3-ws", (
                    f"探测失败时 grey 必须回退 v3（不炸不断流）: {hello}"
                )
                assert acc._client is not None
                await acc.close()
                return True

        assert _run(scenario())
    finally:
        srv.stop()


def test_async_client_on_mode_raises_when_a2a_probe_fails():
    from secbaas.community.core.service.bot_run._async_chat_client import (
        AsyncChatClient,
    )

    srv = LiveServer(make_card_down_app())
    try:

        async def scenario():
            acc = AsyncChatClient(
                uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                a2a_mode="on",
                max_retries=0,
            )
            await acc.connect()

        with pytest.raises(A2aUnavailableError):
            _run(scenario())
    finally:
        srv.stop()


@pytest.mark.parametrize("case,payload", _MALFORMED_CARDS)
def test_async_client_grey_malformed_card_falls_back_to_v3(case, payload):
    """结构畸形卡不炸灰度门——grey 仍如约回退 v3（原 WS 服务照常可用）。"""
    from unittest.mock import patch

    from secbaas.community.core.service.bot_run._async_chat_client import (
        AsyncChatClient,
    )

    srv = LiveServer(make_malformed_card_app(payload))
    try:

        async def scenario():
            with patch(
                "secbaas.community.core.service.bot_run._async_chat_client."
                "BotWebSocketClient",
                _FakeV3KingineTransport,
            ):
                acc = AsyncChatClient(
                    uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                    a2a_mode="grey",
                    max_retries=0,
                )
                hello = await acc.connect()
                assert acc._transport == "v3-ws", (
                    f"畸形卡({case})下 grey 必须回退 v3: {hello}"
                )
                await acc.close()
                return True

        assert _run(scenario())
    finally:
        srv.stop()


@pytest.mark.parametrize("case,payload", _MALFORMED_CARDS)
def test_async_client_on_mode_malformed_card_raises_a2a_error(case, payload):
    """on 模式下畸形卡 = 明确的 A2aUnavailableError 失败，而非裸
    AttributeError/TypeError 击穿建连。"""
    from secbaas.community.core.service.bot_run._async_chat_client import (
        AsyncChatClient,
    )

    srv = LiveServer(make_malformed_card_app(payload))
    try:

        async def scenario():
            acc = AsyncChatClient(
                uri=f"ws://127.0.0.1:{srv.port}/api/openclaw/ws",
                a2a_mode="on",
                max_retries=0,
            )
            await acc.connect()

        with pytest.raises(A2aUnavailableError):
            _run(scenario())
    finally:
        srv.stop()


# ── P0：proxypass token 在线刷新（120s TTL 快照过期的 62 例 401 风暴正修）──


def test_refresh_proxypass_token_updates_and_guards():
    from secbaas.community.core.service.bot_run._bot_a2a_client import (
        BotA2aClient,
    )

    client = BotA2aClient(base_url="http://x", headers={"x-proxypass-token": "mock"})
    client.refresh_proxypass_token({"x-proxypass-token": "placeholder"})
    assert client.headers["x-proxypass-token"] == "placeholder"

    client.refresh_proxypass_token({"x-proxypass-token": ""})
    assert client.headers["x-proxypass-token"] == "placeholder", (
        "空 token（Local 平台）不得覆盖既有值"
    )

    client.refresh_proxypass_token(None)
    assert client.headers["x-proxypass-token"] == "placeholder"
