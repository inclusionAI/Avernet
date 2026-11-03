"""async chat a2a test support."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
)
from tests.utils.chat_transport import make_chat_client


class _StubV3Transport:
    """BotWebSocketClient 的 duck 替身——仅测试注入重路由消费的面。

    R3-2 扩展：记录断连处理器并可模拟 recv_loop finally 的断连通知
    （connected=False + 回调），供 fallback 断连收口/重建场景使用。
    """

    instances: list["_StubV3Transport"] = []

    def __init__(self, uri, client_id=None, client_version="1.0.0", headers=None):
        self.uri = uri
        self.connected = True
        self._event_handlers: dict = {}
        self.inject_calls: list[tuple[str, tuple, dict]] = []
        self.chat_send_calls: list[tuple[str, tuple, dict]] = []
        self.chat_abort_calls: list[tuple[str, dict]] = []
        self.close_calls = 0
        self.disconnect_handler = None
        _StubV3Transport.instances.append(self)

    async def connect(self) -> dict:
        return {"ok": True, "payload": {"server": {}}}

    def on_event(self, name: str, handler) -> None:
        self._event_handlers[name] = handler

    def on_disconnect(self, handler) -> None:
        self.disconnect_handler = handler

    def simulate_disconnect(self) -> None:
        """模拟 recv_loop finally：连接失效 + 触发断连处理器。"""
        self.connected = False
        if self.disconnect_handler is not None:
            self.disconnect_handler("disconnect", {})

    async def chat_inject(self, session_key: str, *args, **kwargs) -> dict:
        self.inject_calls.append((session_key, args, kwargs))
        return {"ok": True}

    async def chat_send(self, session_key: str, *args, **kwargs) -> dict:
        self.chat_send_calls.append((session_key, args, kwargs))
        return {"ok": True}

    async def chat_abort(self, session_key: str, run_id=None) -> dict:
        self.chat_abort_calls.append((session_key, {"run_id": run_id}))
        return {"ok": True}

    async def close(self) -> None:
        self.close_calls += 1
        self.connected = False


@pytest.fixture
def stub_v3_patch():
    _StubV3Transport.instances = []
    with patch(
        "secbaas.community.core.service.bot_run._async_chat_client_ws.BotWebSocketClient",
        _StubV3Transport,
    ):
        yield
    _StubV3Transport.instances = []


def _a2a_acc_with_stub_main_client() -> AsyncChatClient:
    """A2A 策略 + 可收口的替身主连接（close 可 await）。"""
    acc = make_chat_client(uri="ws://host/ws", max_retries=0, transport_mode="grey")
    acc._chat_transport._client = MagicMock()
    acc._chat_transport._client.connected = True
    acc._chat_transport._client.close = AsyncMock()
    acc._chat_transport._current_name = "a2a"  # 已连接的 A2A 策略测试面
    return acc


class _GatedV3Transport(_StubV3Transport):
    """慢建连替身：connect 闸在类级事件上——复现并发首发都看到
    `_v3_fallback is None` 的竞态窗口（R7-F4）。"""

    release: asyncio.Event
    gated_instances: list["_GatedV3Transport"] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _GatedV3Transport.gated_instances.append(self)

    async def connect(self) -> dict:
        await self.release.wait()
        self.connected = True
        return {"ok": True, "payload": {"server": {}}}


class _TerminalOnSendV3(_StubV3Transport):
    """终态先于 ACK 替身：chat_send 在返回前就让 WS 接收循环把主 run
    的 final 帧分派出去——复现"终态在 chat_send() 等待方恢复前到达"
    的时序（R8-F3）。"""

    async def chat_send(self, session_key, *args, **kwargs):
        self._event_handlers["chat"](
            {
                "sessionKey": session_key,
                "state": "final",
                "message": {"content": [{"type": "text", "text": "all done"}]},
            }
        )
        self.chat_send_calls.append((session_key, args, kwargs))
        return {"ok": True}
