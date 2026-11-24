"""a2a test support wire; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Request


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

    asyncio.run(_consume_wire_events_(client, wire_events, rid))


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
