"""a2a test support cancel; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from a2a_test_support_base import (
    _sse,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

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
