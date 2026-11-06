"""test async chat a2a selection; focused regression tests and fixtures."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_interaction import (
    InteractionResolution,
)
from tests.utils.chat_transport import make_chat_client

from ._async_chat_coverage_support import (
    mock_bot_ws as mock_bot_ws,
)
from ._async_chat_interaction_support import (
    _claim_service_for,
    _make_exchange,
)


class TestA2aBaseUrlFromWs:
    """ws(s):// URL → A2A HTTP base：只剥 WS endpoint 后缀，保留代理前缀。

    WS 后缀词汇集（_claw_service._build_ws_url / adapter.ws_path()）：
      /api/openclaw/ws       — openclaw adapter + DEFAULT_WS_PATH
      /api/claude_code/ws    — claude_code adapter
      /api/ws                — aicoding adapter
      /api/{engine}/ws      — 无 adapter 引擎的默认
      /ws                    — 直连形态（无前缀）
    """

    def test_proxypass_shape_keeps_route_prefix_wss(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        uri = "wss://proxy.example/proxypass/sbx-1:20003/api/openclaw/ws"
        assert (
            _a2a_base_url_from_ws(uri) == "https://proxy.example/proxypass/sbx-1:20003"
        )

    def test_proxypass_shape_keeps_route_prefix_ws(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        uri = "ws://proxy.example/proxypass/sbx-1/api/claude_code/ws"
        assert _a2a_base_url_from_ws(uri) == "http://proxy.example/proxypass/sbx-1"

    def test_aicoding_custom_adapter_ws_path_shape(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        # aicoding adapter 的 ws_path=/api/ws：剥后不得把 /api 留在 base 上
        uri = "wss://proxy.example/proxypass/sbx-1/api/ws"
        assert _a2a_base_url_from_ws(uri) == "https://proxy.example/proxypass/sbx-1"

    def test_default_engine_path_shape(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        uri = "ws://proxy.example/proxypass/sbx-1/api/teclaw/ws"
        assert _a2a_base_url_from_ws(uri) == "http://proxy.example/proxypass/sbx-1"

    def test_direct_shape_no_prefix_strips_to_bare_base(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        assert _a2a_base_url_from_ws("ws://host:20003/ws") == "http://host:20003"

    def test_direct_shape_no_path(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        assert _a2a_base_url_from_ws("ws://host:20003") == "http://host:20003"

    def test_non_ws_scheme_returns_none(self):
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        assert _a2a_base_url_from_ws("http://host/api/openclaw/ws") is None
        assert _a2a_base_url_from_ws("not-a-url") is None
        assert _a2a_base_url_from_ws("") is None


class TestInteractionAnswerTransportAttribution:
    """B3 附件 run 建在 v3 专用连接上——它的 ResolveInteraction 也必须走
    v3：A2A kernel 注册表里没有该 run，错发 A2A 得 not_found、审批永不
    恢复。正常 A2A session 的答复走 A2A 客户端，并带 session 标识（供
    run 上下文取 IAM token，P1-5）。off/v3 模式恒走 self._client 且签名
    原样（现行契约）。"""

    @pytest.mark.asyncio
    async def test_rerouted_session_answer_goes_to_v3_fallback(self, mock_bot_ws):
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        service = _claim_service_for(resolution)
        acc = make_chat_client(
            uri="ws://host/ws",
            max_retries=0,
            interaction_service=service,
            transport_mode="grey",
        )
        acc._chat_transport._current_name = "a2a"
        a2a_client = MagicMock(name="a2a")
        a2a_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        v3_client = MagicMock(name="v3")
        v3_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        acc._chat_transport._client = a2a_client
        acc._chat_transport.fallback_client = v3_client
        acc._chat_transport.routed_sessions["sk1"] = "v3"  # B3 归属：附件 reroute 过

        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )

        v3_client.interaction_resolve.assert_awaited_once_with(
            interaction_id="int-1", resolution=resolution
        )
        a2a_client.interaction_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_normal_a2a_session_answer_goes_to_a2a_with_session_key(
        self, mock_bot_ws
    ):
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        service = _claim_service_for(resolution)
        acc = make_chat_client(
            uri="ws://host/ws",
            max_retries=0,
            interaction_service=service,
            transport_mode="grey",
        )
        acc._chat_transport._current_name = "a2a"
        a2a_client = MagicMock(name="a2a")
        a2a_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        v3_client = MagicMock(name="v3")
        v3_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        acc._chat_transport._client = a2a_client
        acc._chat_transport.fallback_client = v3_client

        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )

        a2a_client.interaction_resolve.assert_awaited_once_with(
            interaction_id="int-1", resolution=resolution, session_key="sk1"
        )
        v3_client.interaction_resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rerouted_session_without_fallback_marks_failed(self, mock_bot_ws):
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        service = _claim_service_for(resolution)
        acc = make_chat_client(
            uri="ws://host/ws",
            max_retries=0,
            interaction_service=service,
            transport_mode="grey",
        )
        acc._chat_transport._current_name = "a2a"
        acc._chat_transport._client = MagicMock(name="a2a")
        acc._chat_transport.routed_sessions["sk1"] = "v3"
        acc._chat_transport.fallback_client = None  # fallback 已收口

        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )

        service.mark_failed.assert_called_once()
        assert service.mark_failed.call_args.kwargs["session_key"] == "sk1"

    @pytest.mark.asyncio
    async def test_off_mode_signature_stays_unchanged(self, mock_bot_ws):
        """off/v3 模式：恒走 self._client、签名不带 session_key（现行契约
        test_interaction_dispatch_uses_typed_claim_command 的精确钉）。"""
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        service = _claim_service_for(resolution)
        acc = make_chat_client(
            uri="ws://host/ws", max_retries=0, interaction_service=service
        )
        engine_client = MagicMock()
        engine_client.interaction_resolve = AsyncMock(return_value=_make_exchange())
        acc._chat_transport._client = engine_client  # off 模式 v3-ws
        assert not hasattr(acc._chat_transport, "routed_sessions")

        await acc._dispatch_interaction_answer(
            session_key="sk1", interaction_id="int-1", deadline_ms=None
        )

        engine_client.interaction_resolve.assert_awaited_once_with(
            interaction_id="int-1", resolution=resolution
        )
        assert (
            engine_client.interaction_resolve.await_args.kwargs.get("session_key")
            is None
        )


class TestA2aModeValidation:
    def test_invalid_a2a_mode_is_rejected_at_construct(self, mock_bot_ws):
        """非法灰度模式在构造期即拒——不落到中途探测时才炸。"""

        with pytest.raises(ValueError, match="invalid transport_mode"):
            make_chat_client(uri="ws://host/ws", transport_mode="bogus")

    @pytest.mark.parametrize("mode", ["off", "grey", "on"])
    def test_valid_modes_are_accepted(self, mode, mock_bot_ws):

        client = make_chat_client(uri="ws://host/ws", transport_mode=mode)
        assert getattr(client._chat_transport, "mode", "off") == mode

    def test_constructor_selects_transport_strategy_once(self, mock_bot_ws):
        """off 默认只选 WS；grey/on 只选 A2A，构造后不允许混走另一实现。"""
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            A2aChatTransport,
        )
        from secbaas.community.core.service.bot_run._async_chat_client_ws import (
            WebSocketChatTransport,
        )

        default_client = make_chat_client(uri="ws://host/ws")
        assert isinstance(default_client._chat_transport, WebSocketChatTransport)
        assert not hasattr(default_client._chat_transport, "fallback_client")
        assert not hasattr(default_client._chat_transport, "routed_sessions")

        for mode in ("off", "grey", "on"):
            client = make_chat_client(uri="ws://host/ws", transport_mode=mode)
            assert client.transport_name == "unknown"
            assert not hasattr(client, "_transport")

        for mode in ("grey", "on"):
            client = make_chat_client(uri="ws://host/ws", transport_mode=mode)
            assert isinstance(client._chat_transport, A2aChatTransport)
            assert client._chat_transport.mode == mode

    @pytest.mark.asyncio
    async def test_a2a_reconnect_uses_selected_strategy(self, mock_bot_ws):
        """A2A 断链重连仍走 A2A 策略，不落回硬编码 WS 构造。"""

        def make_a2a_client(**_kwargs):
            instance = MagicMock()
            instance.connected = True
            instance.connect = AsyncMock(return_value=True)
            instance.close = AsyncMock()
            instance.server_info = {"features": {}}
            return instance

        a2a_cls = MagicMock(side_effect=make_a2a_client)
        ws_cls = MagicMock(side_effect=AssertionError("A2A reconnect used WS"))
        with (
            patch(
                "secbaas.community.core.service.bot_run._async_chat_client_a2a."
                "BotA2aClient",
                a2a_cls,
            ),
            patch(
                "secbaas.community.core.service.bot_run._async_chat_client_ws."
                "BotWebSocketClient",
                ws_cls,
            ),
        ):
            client = make_chat_client(
                uri="ws://host/api/openclaw/ws",
                transport_mode="on",
                max_retries=1,
                retry_base_backoff=0,
            )
            await client.connect()
            old_client = client._chat_transport._client

            old_client.connected = False
            client._notify_disconnect()
            await asyncio.sleep(0.02)

            assert a2a_cls.call_count == 2
            assert ws_cls.call_count == 0
            assert client._chat_transport._client is not old_client
            assert client._chat_transport.transport_name == "a2a"
            await client.close()

    @pytest.mark.asyncio
    async def test_a2a_on_without_base_url_raises(self, mock_bot_ws):
        """transport_mode=on 无可推导 HTTP base 时明确失败，不伪装成 WS 成功。"""
        from secbaas.community.core.service.bot_run._bot_a2a_client import (
            A2aUnavailableError,
        )

        client = make_chat_client(uri="ws://host/not-openclaw", transport_mode="on")
        # A non-openclaw/custom path is still convertible to a host base, so
        # force the strategy boundary directly to cover the no-base contract.
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client_a2a."
            "_a2a_base_url_from_ws",
            return_value=None,
        ):
            with pytest.raises(A2aUnavailableError):
                await client.connect()


class TestA2aGreyReconnectFallbackOwnership:
    """grey 重连回退 WS 后，既有 fallback run 仍必须按归属路由。"""

    @pytest.mark.asyncio
    async def test_late_abort_and_interaction_stay_on_fallback(self, mock_bot_ws):
        class _FakeEndpoint:
            connected = True

            def __init__(self, name: str):
                self.name = name
                self.calls: list[str] = []

            async def chat_abort(self, *args, **kwargs):
                self.calls.extend(args)
                return {"ok": True, "endpoint": self.name}

        minter = AsyncMock(side_effect=AssertionError("reconnected WS minted A2A auth"))
        acc = make_chat_client(
            uri="ws://host/api/openclaw/ws",
            transport_mode="grey",
            proxypass_token_minter=minter,
        )
        fallback = _FakeEndpoint("fallback")
        new_main = _FakeEndpoint("new-main-ws")
        acc._chat_transport._client = new_main
        acc._chat_transport._current_name = "v3-ws"
        acc._chat_transport.fallback_client = fallback
        acc._chat_transport.routed_sessions["s-att"] = "v3"
        acc.refresh_outbound_auth({"x-proxypass-token": "new-placeholder"})

        assert await acc.chat_abort("s-att", "r1") == {
            "ok": True,
            "endpoint": "fallback",
        }
        resolution = InteractionResolution(kind="exec", decision="allow-once")
        fallback.interaction_resolve = AsyncMock(return_value=_make_exchange())
        new_main.interaction_resolve = AsyncMock(return_value=_make_exchange())
        await acc._chat_transport.resolve_interaction(
            session_key="s-att", interaction_id="i1", resolution=resolution
        )
        fallback.interaction_resolve.assert_awaited_once_with(
            interaction_id="i1", resolution=resolution
        )
        # 未登记归属的 normal run 仍走重连后的 WS main，不带 A2A session key。
        await acc._chat_transport.resolve_interaction(
            session_key="s-normal", interaction_id="i2", resolution=resolution
        )
        new_main.interaction_resolve.assert_awaited_once_with(
            interaction_id="i2", resolution=resolution
        )
        minter.assert_not_awaited()


class TestA2aBaseUrlUnknownWsPath:
    def test_unknown_custom_ws_path_falls_back_to_bare_host(self):
        """末段非 ``ws`` 的自定义 ws_path：不猜前缀归属——base 维持 host 根，
        由能力探测诚实回退（portal 前缀只在词汇表内剥）。"""
        from secbaas.community.core.service.bot_run._async_chat_client_a2a import (
            _a2a_base_url_from_ws,
        )

        assert (
            _a2a_base_url_from_ws("ws://host:20003/custom/endpoint")
            == "http://host:20003"
        )
