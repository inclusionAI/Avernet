"""test async chat pool auth; focused regression tests and fixtures."""

from unittest.mock import MagicMock

import pytest

from ._async_chat_pool_support import (
    mock_chat_client as mock_chat_client,
)
from ._async_chat_pool_support import (
    mock_chat_client_class as mock_chat_client_class,
)


class TestOutboundAuthRefresh:
    """P0：proxypass JWT TTL 仅 120s，池连接存活远超之——复用路径必须
    转发调用方每条消息现签的最新 token（2026-10-29 预发 62 例过期
    token 401 风暴的正修）。v3 WS 握手后无请求级鉴权，无该方法即跳过。"""

    @pytest.mark.asyncio
    async def test_reuse_forwards_latest_token(self, mock_chat_client_class):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=1)
        mock_cls, mock_client = mock_chat_client_class
        h1 = {"x-proxypass-token": "mock"}
        h2 = {"x-proxypass-token": "placeholder"}
        c1 = await pool.get("sbx-refresh", "ws://h/ws", h1)
        c2 = await pool.get("sbx-refresh", "ws://h/ws", h2)
        assert c1 is c2, "max=1 下必须复用既有连接"
        mock_client.refresh_outbound_auth.assert_called_once_with(h2)
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_session_owner_path_forwards_latest_token(
        self, mock_chat_client_class
    ):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
            _ConnEntry,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=2)
        owner = MagicMock()
        owner.tracks_session = MagicMock(return_value=True)
        pool._clients["sbx-own"] = [_ConnEntry(client=owner, a2a_mode="on")]
        headers = {"x-proxypass-token": "sample"}

        result = await pool.get("sbx-own", "ws://h/ws", headers, session_key="sk-run")
        assert result is owner
        owner.refresh_outbound_auth.assert_called_once_with(headers)
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_empty_headers_skip_refresh(self, mock_chat_client_class):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=1)
        mock_cls, mock_client = mock_chat_client_class
        await pool.get("sbx-empty", "ws://h/ws", None)
        await pool.get("sbx-empty", "ws://h/ws", {"x-proxypass-token": ""})
        mock_client.refresh_outbound_auth.assert_not_called()
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_client_without_refresh_method_is_safe(self, mock_chat_client_class):
        """v3 BotWebSocketClient 无该转发方法（duck-typed）——复用不动 cookie。"""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
            _ConnEntry,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=1)
        v3_like = MagicMock(
            spec=[
                "is_connected",
                "is_reconnecting",
                "has_active_sessions",
                "active_session_count",
            ]
        )
        v3_like.is_connected = True
        v3_like.is_reconnecting = False
        v3_like.active_session_count = 0
        v3_like.has_active_sessions = False
        pool._clients["sbx-v3"] = [_ConnEntry(client=v3_like, a2a_mode="off")]

        result = await pool.get("sbx-v3", "ws://h/ws", {"x-proxypass-token": "t"})
        assert result is v3_like, "无转发方法时照常复用，不得炸"
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_get_passes_caller_minter_to_new_connection(
        self, mock_chat_client_class
    ):
        """per-get 闭包随建连透传（评审 P2：绑定真实签发上下文，池不做
        key 解析）；复用不覆盖既有连接的闭包（建连上下文才是正确的铸造源）。
        """
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=1)
        mock_cls, mock_client = mock_chat_client_class

        async def minter_a() -> str:
            return "a"

        async def minter_b() -> str:
            return "b"

        await pool.get(
            "sbx", "ws://h/ws", {"x-proxypass-token": "t"}, token_minter=minter_a
        )
        kwargs1 = mock_cls.call_args.kwargs
        assert kwargs1["proxypass_token_minter"] is minter_a

        await pool.get(
            "sbx", "ws://h/ws", {"x-proxypass-token": "t"}, token_minter=minter_b
        )  # 复用：不建新连接
        assert mock_cls.call_count == 1, "复用路径不得重建连接"
        assert mock_client.proxypass_token_minter is not None
        await pool.close_all()
