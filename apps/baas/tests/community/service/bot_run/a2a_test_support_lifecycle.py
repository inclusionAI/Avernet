"""a2a test support lifecycle; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from secbaas.community.api.bot_interaction._models import InteractionResolution
from secbaas.community.core.service.bot_run._interaction_protocol import (
    EngineInteractionResolveExchange,
)


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


def _dead_rpc_url() -> str:
    """刚释放的临时端口——连接必拒（保证性的传输失败源，不依赖端口占用）。"""

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
