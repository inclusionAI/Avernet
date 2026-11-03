"""a2a test support engines; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
    _sse,
)
from a2a_test_support_cancel import (
    _cancel_capable_card,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


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
