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
    A2aUnavailableError, BotA2aClient,
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
        return {"supportedInterfaces": [
                    {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
                "version": "0.1.0"}

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        if body["method"] == "SendStreamingMessage":
            session = body["params"]["message"]["contextId"]

            async def gen():
                yield _sse({"statusUpdate": {
                    "taskId": "t1", "contextId": session,
                    "status": {"state": "TASK_STATE_WORKING"},
                    "metadata": {"kind": "agent", "seq": 1,
                                 "original_event": "agent",
                                 "engine_frame": {"stream": "tool",
                                                   "data": {"phase": "start"},
                                                   "state": ""}}}})
                yield _sse({"statusUpdate": {
                    "taskId": "t1", "contextId": session,
                    "status": {"state": "TASK_STATE_WORKING"},
                    "metadata": {"kind": "agent",
                                 "original_event":
                                     "mode_transition.resolved",
                                 "seq": 2,
                                 "engine_frame": {"state": "resolved",
                                                   "mode": "plan"}}}})
                yield _sse({"artifactUpdate": {
                    "taskId": "t1", "contextId": session,
                    "artifact": {"parts": [{"text": "你好"}]},
                    "append": False, "lastChunk": False,
                    "metadata": {"seq": 3}}})
                await asyncio.sleep(final_delay)   # 真流式：delta→final 窗口
                yield _sse({"artifactUpdate": {
                    "taskId": "t1", "contextId": session,
                    "artifact": {"parts": [{"text": "你好，完整终稿"}]},
                    "append": False, "lastChunk": True,
                    "metadata": {"seq": 4}}})
                yield _sse({"statusUpdate": {
                    "taskId": "t1", "contextId": session,
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "metadata": {"final": "true", "lastSeq": 5}}})
            return StreamingResponse(gen(), media_type="text/event-stream")
        if body["method"] == "InjectContext":
            return {"jsonrpc": "2.0", "id": body["id"],
                    "result": {"inject": "accepted"}}
        if body["method"] == "ResolveInteraction":
            err_conflict = (body["params"]["resolution"]
                            .get("decision") == "deny")
            if err_conflict:
                return JSONResponse({"jsonrpc": "2.0", "id": body["id"],
                                    "error": {"code": -32003,
                                              "data": {"reason":
                                                        "resolve_conflict"}}})
            return {"jsonrpc": "2.0", "id": body["id"],
                    "result": {"interaction": {"resolved": True}}}
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"],
                            "error": {"code": -32601}})

    return app


def make_failed_app() -> FastAPI:
    """独立 FAILED 脚本——首个终态即 FAILED（R17-1：不追加于 COMPLETED 后）。"""
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {"supportedInterfaces": [
                    {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]}

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        session = body["params"]["message"]["contextId"]

        async def gen():
            yield _sse({"artifactUpdate": {
                "taskId": "t1", "contextId": session,
                "artifact": {"parts": [{"text": "将失败"}]},
                "append": False, "lastChunk": True,
                "metadata": {"seq": 1}}})
            await asyncio.sleep(0.2)
            yield _sse({"statusUpdate": {
                "taskId": "t1", "contextId": session,
                "status": {"state": "TASK_STATE_FAILED"},
                "metadata": {"final": "true",
                              "engine_frame": {"errorMessage":
                                                "engine broke"},
                              "lastSeq": 2}}})
        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


def make_gate_app() -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {"supportedInterfaces": [
                    {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]}

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"],
                             "error": {"code": -32000,
                                       "message": "auth_required",
                                       "data": {"reason": "auth_required"}}})

    return app


def make_card_app(binding: str, version: str) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {"supportedInterfaces": [
            {"protocolBinding": binding, "protocolVersion": version}]}

    return app


class LiveServer:
    """真 uvicorn TCP 服务器（ephemeral 端口）——流式时序可信。"""

    def __init__(self, app: FastAPI):
        cfg = uvicorn.Config(app, host="127.0.0.1", port=0,
                             log_level="critical")
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self._server.started:
            time.sleep(0.05)
        assert self._server.started
        self.port = (self._server.servers[0].sockets[0]
                     .getsockname()[1])

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
        await asyncio.sleep(1.0)      # body task 消费完
        await client.close()

    _run(scenario())


def test_first_delta_before_final_with_barrier(stream_server):
    """R17-1: 事件屏障——delta 到达时 final 必须尚未出现（真流式专属断言）。"""
    received: list[tuple[str, dict]] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event("chat", lambda n, p: received.append((n, p)))
        client.on_event("agent", lambda n, p: received.append((n, p)))
        client.on_event(
            "mode_transition.resolved",
            lambda n, p: received.append((n, p)))   # R17-2 路由验证
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(0.25)    # final_delay=0.5s 之内
        states = {p.get("state") for _, p in received}
        return states

    states = _run(scenario())
    assert "delta" in states, f"delta missing at barrier: {states}"
    assert "final" not in states, f"final leaked before barrier: {states}"


def test_mode_transition_dispatches_to_original_event_name(stream_server):
    mt: list[dict] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event("mode_transition.resolved",
                        lambda n, p: mt.append(p))
        client.on_event("agent", lambda n, p: mt.append({"__agent__": p}))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(1.0)
        await client.close()

    _run(scenario())
    assert any(p.get("mode") == "plan" for p in mt), \
        f"mode_transition.resolved 未按原事件名路由: {mt}"


# ── T1: 终态单次 ──

def test_double_final_dedup_and_content_survived(stream_server):
    finals: list[dict] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event("chat", lambda n, p: finals.append(p)
                        if p.get("state") == "final" else None)
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
        client.on_event("chat", lambda n, p: seen.append((n, p)))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(1.0)
        await client.close()

    _run(scenario())
    errors = [p for _, p in seen if p.get("state") == "error"]
    finals = [p for _, p in seen if p.get("state") == "final"]
    assert errors and not finals, \
        f"FAILED must be error, not final; seen={[p['state'] for _, p in seen]}"
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
            interaction_id="i-1",
            resolution=InteractionResolution(decision="approve"))

    async def conflict_case():
        client = await _connect_async(stream_server.base)
        return await client.interaction_resolve(
            interaction_id="i-1",
            resolution=InteractionResolution(decision="deny"))

    out = _run(ok_case())
    assert isinstance(out, EngineInteractionResolveExchange)
    assert out.accepted is True and out.error_message is None
    assert out.request["id"] == out.response["id"]        # R17-6 校验

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
        return {"supportedInterfaces": [
            {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]}

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        await request.json()
        return {"wrong": "shape"}     # 无 id、无 result/error

    srv = LiveServer(app)
    try:
        async def scenario():
            client = await _connect_async(srv.base)
            return await client.interaction_resolve(
                interaction_id="i-9",
                resolution=InteractionResolution(decision="approve"))

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
                    raise AssertionError(
                        f"{binding}/{version} must be rejected")
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
        return await client.chat_send(session_key="s", message="hi",
                                     attachments=[{"type": "image"}])

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
        return {"supportedInterfaces": [
            {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]}

    @cap_app.post("/rpc")
    async def rpc(request: Request) -> Any:
        body = await request.json()
        captured.update(body)
        return JSONResponse({"jsonrpc": "2.0", "id": body["id"],
                            "error": {"code": -32600}})

    srv = LiveServer(cap_app)
    try:
        async def scenario():
            client = await _connect_async(srv.base)
            await client.chat_send(
                session_key="s", message="hi", timeout_ms=4500,
                app_id="app-77",
                chat_metadata={"permissionMode": "bypassPermissions",
                               "source": "e2e"})

        _run(scenario())
        meta = captured["params"]["metadata"]
        assert meta["permissionMode"] == "bypassPermissions"   # 平铺键
        assert meta["source"] == "e2e"
        assert meta["appId"] == "app-77"
        assert meta["timeoutMs"] == "4500"
    finally:
        srv.stop()