"""a2a test support inject; focused regression tests and fixtures."""

from __future__ import annotations

from typing import Any

from a2a_test_support_base import (
    LiveServer,
    _connect_async,
)
from fastapi import FastAPI, Request


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
