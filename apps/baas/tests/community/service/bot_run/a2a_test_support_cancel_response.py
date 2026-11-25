"""a2a test support cancel response; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from a2a_test_support_base import (
    _sse,
)
from a2a_test_support_cancel import (
    _cancel_capable_card,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


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
