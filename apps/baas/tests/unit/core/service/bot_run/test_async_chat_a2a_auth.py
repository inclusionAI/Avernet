"""test async chat a2a auth; focused regression tests and fixtures."""

import time as _time
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.utils.chat_transport import make_chat_client

from ._async_chat_test_support import (
    mock_bot_ws as mock_bot_ws,
)


class TestRefreshOutboundAuth:
    """A2A 传输的 x-proxypass-token TTL 仅 120s——池层逐消息转发的最新
    头必须转给内部 A2A 子客户端；v3 WS/None 子客户端天然 no-op。"""

    def test_forwards_to_a2a_client(self, mock_bot_ws):

        client = make_chat_client(uri="ws://h/ws", headers={}, transport_mode="grey")
        a2a_like = MagicMock()
        client._chat_transport._current_name = "a2a"
        client._chat_transport._client = a2a_like
        headers = {"x-proxypass-token": "placeholder"}
        client.refresh_outbound_auth(headers)
        a2a_like.refresh_proxypass_token.assert_called_once_with(headers)

    def test_noop_when_client_has_no_method(self, mock_bot_ws):

        client = make_chat_client(uri="ws://h/ws", headers={}, transport_mode="off")
        v3_like = MagicMock(spec=["is_connected", "close", "connect"])
        client._chat_transport._client = v3_like
        client.refresh_outbound_auth({"x-proxypass-token": "x"})  # 不得炸

    def test_noop_when_client_is_none(self, mock_bot_ws):

        client = make_chat_client(uri="ws://h/ws", headers={}, transport_mode="grey")
        client._chat_transport._client = None
        client.refresh_outbound_auth({"x-proxypass-token": "x"})  # 不得炸


class TestRefreshSyncsOuterHeaders:
    """外层 headers 必须随刷新同步——v3 附件 fallback 的懒建握手
    （_ensure_v3_fallback）读的是外层快照，同步漏了附件 WS 就携带
    过期 token（评审 P2 场景 1）。"""

    def test_refresh_syncs_outer_headers_snapshot(self, mock_bot_ws):

        client = make_chat_client(
            uri="ws://h/ws",
            headers={"x-proxypass-token": "mock"},
            transport_mode="grey",
        )
        a2a_like = MagicMock()
        client._chat_transport._current_name = "a2a"
        client._chat_transport._client = a2a_like
        client.refresh_outbound_auth({"x-proxypass-token": "placeholder"})
        assert client._chat_transport.headers["x-proxypass-token"] == "placeholder", (
            "外层快照必须同步——附件 fallback 握手依赖它"
        )
        a2a_like.refresh_proxypass_token.assert_called_once_with(
            {"x-proxypass-token": "placeholder"}
        )

    def test_refresh_ignores_empty_token_for_outer_snapshot(self, mock_bot_ws):

        client = make_chat_client(
            uri="ws://h/ws",
            headers={"x-proxypass-token": "mock"},
            transport_mode="grey",
        )
        client.refresh_outbound_auth({"x-proxypass-token": ""})
        client.refresh_outbound_auth(None)
        assert client._chat_transport.headers["x-proxypass-token"] == "mock"


class TestInteractionDispatchRemint:
    """交互答复后台派发不经过 pool.get——ResolveInteraction 发 RPC 前
    必须经注入的 minter 现签 token（评审 P2 场景 2）；铸造失败回退
    last-known，不得阻断派发。"""

    @staticmethod
    def _make_client(minter):

        from secbaas.community.core.service.bot_run._interaction_protocol import (
            EngineInteractionResolveExchange,
        )

        client = make_chat_client(
            uri=("wss://proxy.example/proxypass/sandbox/api/openclaw/ws"),
            headers={},
            transport_mode="grey",
            proxypass_token_minter=minter,
        )
        client._chat_transport._current_name = "a2a"
        a2a = MagicMock()
        a2a.refresh_proxypass_token = MagicMock()
        a2a.interaction_resolve = AsyncMock(
            return_value=EngineInteractionResolveExchange(
                request={"req": 1},
                response={"resp": 1},
                accepted=True,
                error_message=None,
            )
        )
        client._chat_transport._client = a2a

        svc = MagicMock()
        cmd = MagicMock()
        cmd.resolution.kind = "exec"
        svc.claim_for_dispatch.return_value = cmd
        client._interaction_service = svc
        return client, a2a, svc

    @pytest.mark.asyncio
    async def test_remints_before_resolve(self, mock_bot_ws):
        minter = AsyncMock(return_value="sample")
        client, a2a, svc = self._make_client(minter)

        await client._dispatch_interaction_answer(
            session_key="sk",
            interaction_id="i1",
            deadline_ms=int(_time.time() * 1000) + 60_000,
        )
        minter.assert_awaited_once()
        a2a.refresh_proxypass_token.assert_called_once_with(
            {"x-proxypass-token": "sample"}
        )
        assert client._chat_transport.headers["x-proxypass-token"] == "sample"
        svc.should_poll.assert_not_called(), "成功派发后 task 应终结"
        assert a2a.interaction_resolve.await_count == 1

    @pytest.mark.asyncio
    async def test_minter_failure_falls_back_without_blocking(self, mock_bot_ws):
        minter = AsyncMock(side_effect=RuntimeError("mint down"))
        client, a2a, svc = self._make_client(minter)

        await client._dispatch_interaction_answer(
            session_key="sk",
            interaction_id="i1",
            deadline_ms=int(_time.time() * 1000) + 60_000,
        )
        a2a.refresh_proxypass_token.assert_not_called()
        assert a2a.interaction_resolve.await_count == 1, (
            "铸造失败回退 last-known token，不得阻断派发"
        )

    @pytest.mark.asyncio
    async def test_no_minter_falls_back_without_blocking(self, mock_bot_ws):
        client, a2a, svc = self._make_client(None)

        await client._dispatch_interaction_answer(
            session_key="sk",
            interaction_id="i1",
            deadline_ms=int(_time.time() * 1000) + 60_000,
        )
        assert a2a.interaction_resolve.await_count == 1
