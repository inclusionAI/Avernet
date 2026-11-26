"""a2a test support base; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from secbaas.community.core.service.bot_run._bot_a2a_client import (
    BotA2aClient,
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
