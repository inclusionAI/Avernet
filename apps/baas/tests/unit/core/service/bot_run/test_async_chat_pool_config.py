"""test async chat pool config; focused regression tests and fixtures."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from ._async_chat_pool_support import (
    _FakeChatClient,
    _mode_flip_config_service,
)
from ._async_chat_pool_support import (
    mock_chat_client as mock_chat_client,
)
from ._async_chat_pool_support import (
    mock_chat_client_class as mock_chat_client_class,
)


class TestModeFlipReuseValidation:
    """P1-3：灰度配置翻转必须对存量连接生效。

    快路径复用不能只看连接可用性——连接带有建连时的 a2a_mode 快照，
    与当前配置不一致的连接停止承接新请求：空闲的立即收口关闭，
    在途的排空后由下次 get() 关闭。off 保持不变时快路径必须原样复用
    （off 零变化原则）。
    """

    @pytest.fixture
    def fake_client_class(self):
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client_pool.AsyncChatClient",
            _FakeChatClient,
        ):
            _FakeChatClient.reset_create_count()
            yield
            _FakeChatClient.reset_create_count()

    @pytest.mark.asyncio
    async def test_off_grey_off_on_same_pool_instance(self, fake_client_class):
        """同一 pool 实例三段翻转：off → grey → off。每段都不得复用上一段
        模式的连接（即使其空闲健康），且空闲的旧连接应被关闭。"""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        holder = {"value": "off"}
        pool = AsyncChatClientPool(
            max_conns_per_sandbox=1,
            system_config_service=_mode_flip_config_service(holder),
        )

        c1 = await pool.get("sbx-flip", "ws://host/ws")
        assert c1.transport_mode == "off"

        holder["value"] = "grey"
        await asyncio.sleep(0)  # holder 翻转（side_effect 每调用现读）
        c2 = await pool.get("sbx-flip", "ws://host/ws")
        assert c2 is not c1, "grey 开启后不得继续复用 off 存量连接"
        assert c2.transport_mode == "grey"
        await asyncio.sleep(0.01)
        assert c1._connected is False, "空闲 off 连接在 grey 翻转后应立即关闭"

        holder["value"] = "off"
        c3 = await pool.get("sbx-flip", "ws://host/ws")
        assert c3 is not c2, "off 回退后不得继续复用 grey 存量连接"
        assert c3.transport_mode == "off"
        await asyncio.sleep(0.01)
        assert c2._connected is False, "空闲 grey 连接在 off 回退后应立即关闭"

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_stale_connection_with_inflight_requests_drains_then_closes(
        self, fake_client_class
    ):
        """在途请求期间翻转：在途的旧模式连接不被误杀；排空后由下次
        get() 收口关闭，且不再承接新请求。"""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        holder = {"value": "off"}
        pool = AsyncChatClientPool(
            max_conns_per_sandbox=1,
            system_config_service=_mode_flip_config_service(holder),
        )

        c1 = await pool.get("sbx-busy", "ws://host/ws")
        c1._active = 1  # 在途

        holder["value"] = "grey"
        c2 = await pool.get("sbx-busy", "ws://host/ws")
        assert c2 is not c1
        assert c2.transport_mode == "grey"
        assert c1._connected is True, "在途请求期间不得误杀旧连接"

        c1._active = 0  # 排空
        c3 = await pool.get("sbx-busy", "ws://host/ws")
        assert c3 is c2, "排空后的旧模式连接不得被再次选中，新模式连接应复用"
        await asyncio.sleep(0.01)
        assert c1._connected is False, "排空后的旧模式连接应被收口关闭"
        entries = pool._clients.get("sbx-busy", [])
        assert len(entries) == 1 and entries[0].client is c2

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_unchanged_mode_reuses_idle_connection(self, fake_client_class):
        """不变模式（off 与 grey 同理）下快路径必须原样复用——翻转校验
        不得引入连接抖动（off 零变化原则）。"""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )
        from secbaas.community.core.service.config import SystemConfigKey

        holder = {"value": "off"}
        service = _mode_flip_config_service(holder)
        pool = AsyncChatClientPool(
            max_conns_per_sandbox=1, system_config_service=service
        )

        c1 = await pool.get("sbx-stable", "ws://host/ws")
        c2 = await pool.get("sbx-stable", "ws://host/ws")
        c3 = await pool.get("sbx-stable", "ws://host/ws")
        assert c1 is c2 is c3
        assert _FakeChatClient.get_create_count() == 1

        holder["value"] = "grey"
        c4 = await pool.get("sbx-stable", "ws://host/ws")
        c5 = await pool.get("sbx-stable", "ws://host/ws")
        assert c4 is not c3 and c4 is c5
        # 翻转校验只在 get() 决策时读 A2A_CLIENT_MODE（每 get 恰一次），
        # 不做额外轮询
        mode_reads = [
            c
            for c in service.get_config.call_args_list
            if c.args[0] == SystemConfigKey.A2A_CLIENT_MODE
        ]
        assert len(mode_reads) == 5

        await pool.close_all()


class TestReadA2aModeFailure:
    """配置读取面异常——_read_a2a_mode 吞掉并回退 "off"（1b 灰度门有界：
    配置面故障不得级联成建连失败，off 生产零变化语义在故障下保持成立）。"""

    def test_get_config_raises_returns_off(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        svc = MagicMock()
        svc.get_config.side_effect = RuntimeError("db error")
        assert AsyncChatClientPool._read_a2a_mode(svc) == "off"

    @pytest.mark.asyncio
    async def test_config_read_error_defaults_new_clients_to_off(
        self, mock_chat_client_class
    ):
        mock_cls, _ = mock_chat_client_class

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )
        from secbaas.community.core.service.config import SystemConfigKey

        def fake_get_config(key):
            if key == SystemConfigKey.A2A_CLIENT_MODE:
                raise RuntimeError("config store unavailable")
            return MagicMock(conf_value="false")

        config_service = MagicMock()
        config_service.get_config.side_effect = fake_get_config
        pool = AsyncChatClientPool(system_config_service=config_service)
        await pool.get("sbx-cfg-down", "ws://host/ws")

        assert mock_cls.call_args.kwargs["transport_mode"] == "off"
        await pool.close_all()


class TestLoadSensitiveBypassKeys:
    def test_none_service_returns_empty(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool()
        assert pool._load_sensitive_bypass_keys(None) == ()

    def test_parses_comma_and_newline(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool()
        service = MagicMock()
        service.get_config.return_value = MagicMock(conf_value="abc, def\nghi,,")
        assert pool._load_sensitive_bypass_keys(service) == (
            "abc",
            "def",
            "ghi",
        )

    def test_queries_config_key(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )
        from secbaas.community.core.service.config import SystemConfigKey

        pool = AsyncChatClientPool()
        service = MagicMock()
        service.get_config.return_value = None
        pool._load_sensitive_bypass_keys(service)
        service.get_config.assert_called_once_with(
            SystemConfigKey.SENSITIVE_BYPASS_SESSION_KEYS
        )

    def test_cached_within_ttl(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool()
        service = MagicMock()
        service.get_config.return_value = MagicMock(conf_value="abc")
        pool._load_sensitive_bypass_keys(service)
        pool._load_sensitive_bypass_keys(service)
        assert service.get_config.call_count == 1

    def test_not_configured_cached_within_ttl(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool()
        service = MagicMock()
        service.get_config.return_value = None
        pool._load_sensitive_bypass_keys(service)
        pool._load_sensitive_bypass_keys(service)
        assert service.get_config.call_count == 1

    def test_requeries_after_ttl_expiry(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool()
        service = MagicMock()
        service.get_config.return_value = MagicMock(conf_value="abc")
        assert pool._load_sensitive_bypass_keys(service) == ("abc",)
        # 模拟缓存过期
        pool._bypass_keys_cache = (float("-inf"), ())
        assert pool._load_sensitive_bypass_keys(service) == ("abc",)
        assert service.get_config.call_count == 2

    def test_read_failure_returns_empty(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool()
        service = MagicMock()
        service.get_config.side_effect = RuntimeError("db down")
        assert pool._load_sensitive_bypass_keys(service) == ()
