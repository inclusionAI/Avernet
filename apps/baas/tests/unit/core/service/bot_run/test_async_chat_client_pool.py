"""test async chat client pool; focused regression tests and fixtures."""

import asyncio
from unittest.mock import ANY, MagicMock, patch

import pytest

from ._async_chat_pool_support import (
    _FakeChatClient,
)
from ._async_chat_pool_support import (
    mock_chat_client as mock_chat_client,
)
from ._async_chat_pool_support import (
    mock_chat_client_class as mock_chat_client_class,
)


class TestConstructor:
    def test_defaults(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool()
        assert pool._max_size == 100
        assert pool._max_conns_per_sandbox == 1
        assert pool._max_concurrent_per_conn == 0
        assert pool._session_key_timeout == 30.0
        assert pool._max_retries == 1
        assert pool._retry_base_backoff == 0.5

    def test_custom_params(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(
            max_size=50,
            max_conns_per_sandbox=5,
            max_concurrent_per_conn=3,
            session_key_timeout=10.0,
            max_retries=2,
            retry_base_backoff=1.0,
        )
        assert pool._max_size == 50
        assert pool._max_conns_per_sandbox == 5
        assert pool._max_concurrent_per_conn == 3
        assert pool._session_key_timeout == 10.0
        assert pool._max_retries == 2
        assert pool._retry_base_backoff == 1.0


class TestParamPassthrough:
    @pytest.mark.asyncio
    async def test_params_passed_to_client(self, mock_chat_client_class):
        """New pool params are passed to created AsyncChatClient instances."""
        mock_cls, mock_client = mock_chat_client_class

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(
            max_concurrent_per_conn=5,
            session_key_timeout=15.0,
            max_retries=3,
            retry_base_backoff=2.0,
        )
        await pool.get("sandbox-1", "ws://host/ws")

        mock_cls.assert_called_once_with(
            uri="ws://host/ws",
            headers=None,
            max_concurrent_sessions=5,
            session_key_timeout=15.0,
            max_retries=3,
            retry_base_backoff=2.0,
            transport_mode="off",
            ignore_case=False,
            interaction_service=None,
            sensitive_bypass_keys_loader=ANY,
            proxypass_token_minter=None,
        )

        # 未注入 system_config_service 时，loader 返回空豁免列表
        loader = mock_cls.call_args.kwargs["sensitive_bypass_keys_loader"]
        assert loader() == ()

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_enabled_interaction_service_passed_to_new_client(
        self, mock_chat_client_class
    ):
        mock_cls, _ = mock_chat_client_class

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        config_service = MagicMock()
        # 构造调用按序读三键：A2A_CLIENT_MODE → SESSION_KEY_IGNORE_CASE →
        # INTERACTION_PROCESS（B2 工厂配置门），side_effect 按位序供给。
        config_service.get_config.side_effect = [
            MagicMock(conf_value="off"),
            MagicMock(conf_value="false"),
            MagicMock(conf_value="true"),
        ]
        interaction_service = MagicMock()
        pool = AsyncChatClientPool(
            system_config_service=config_service,
            interaction_service=interaction_service,
        )

        await pool.get("sandbox-1", "ws://host/ws")

        assert mock_cls.call_args.kwargs["interaction_service"] is interaction_service
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_disabled_interaction_service_not_passed_to_new_client(
        self, mock_chat_client_class
    ):
        mock_cls, _ = mock_chat_client_class

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        config_service = MagicMock()
        config_service.get_config.side_effect = [
            MagicMock(conf_value="false"),
            MagicMock(conf_value="false"),
        ]
        pool = AsyncChatClientPool(
            system_config_service=config_service,
            interaction_service=MagicMock(),
        )

        await pool.get("sandbox-1", "ws://host/ws")

        assert mock_cls.call_args.kwargs["interaction_service"] is None
        await pool.close_all()


class TestReconnectAwareness:
    @pytest.mark.asyncio
    async def test_pick_idle_skips_reconnecting(self, mock_chat_client_class):
        """_pick_idle should skip connections that are reconnecting."""
        mock_cls, mock_client = mock_chat_client_class

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=2)
        await pool.get("sandbox-1", "ws://host/ws")

        # 模拟连接正在重连
        mock_client.is_reconnecting = True

        # _pick_idle 应该返回 None（跳过重连中的连接）
        result = pool._pick_idle("sandbox-1")
        assert result is None

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_pick_least_sessions_skips_reconnecting(self, mock_chat_client_class):
        """_pick_least_sessions should skip reconnecting connections."""
        mock_cls, mock_client = mock_chat_client_class

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
            _ConnEntry,
        )

        pool = AsyncChatClientPool()

        # 创建一个重连中的 entry
        reconnecting_client = MagicMock()
        reconnecting_client.is_connected = True
        reconnecting_client.is_reconnecting = True
        reconnecting_client.active_session_count = 0

        # 创建一个正常的 entry
        normal_client = MagicMock()
        normal_client.is_connected = True
        normal_client.is_reconnecting = False
        normal_client.active_session_count = 2

        entries = [
            _ConnEntry(client=reconnecting_client),
            _ConnEntry(client=normal_client),
        ]

        result = pool._pick_least_sessions(entries)
        # 应该跳过重连中的，选择正常的
        assert result is normal_client

    @pytest.mark.asyncio
    async def test_remove_unhealthy_removes_reconnecting(self, mock_chat_client_class):
        """_remove_unhealthy removes reconnecting connections."""
        mock_cls, mock_client = mock_chat_client_class

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=2)
        await pool.get("sandbox-1", "ws://host/ws")

        # 模拟连接正在重连
        mock_client.is_reconnecting = True

        pool._remove_unhealthy("sandbox-1")

        # 重连中的连接应该被移除
        assert "sandbox-1" not in pool._clients

        await pool.close_all()


class TestSessionRoutedGet:
    """取消/关联操作绝不能走负载均衡分配——A2A 的 CancelTask 依赖建连
    实例内的 engine_task_id 记录，发到别的连接就是 RUN_NOT_TRACKED。"""

    @pytest.mark.asyncio
    async def test_session_owner_wins_even_when_busy(self, mock_chat_client_class):
        """默认 max_conns_per_sandbox=2 下"唯一在跑请求 + 取消"原本会扩
        出第二条无记录的连接——session_routed get 必须直落持有者，零新建。"""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
            _ConnEntry,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=2)
        owner = MagicMock()
        owner.tracks_session = MagicMock(return_value=True)
        pool._clients["sbx-1"] = [_ConnEntry(client=owner, a2a_mode="on")]

        result = await pool.get("sbx-1", "ws://host/ws", None, session_key="sk-run")

        assert result is owner, "取消必须命中持有者连接,不得新建/扩容"
        mock_chat_client_class[0].assert_not_called()
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_mode_stale_owner_is_still_routed(self, mock_chat_client_class):
        """模式翻转后仍在途的旧连接（mode-stale）继续服务它的 run——归
        属查找不校验连接模式快照。"""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
            _ConnEntry,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=2)
        owner = MagicMock()
        owner.tracks_session = MagicMock(return_value=True)
        pool._clients["sbx-2"] = [_ConnEntry(client=owner, a2a_mode="on")]

        result = await pool.get("sbx-2", "ws://host/ws", None, session_key="sk-run")
        assert result is owner
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_no_owner_falls_back_to_plain_allocation(
        self, mock_chat_client_class
    ):
        """无归属（未开跑/已收口）→ 正常分配路径行为不变。"""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=2)
        result = await pool.get(
            "sbx-3", "ws://host/ws", None, session_key="ghost-session"
        )
        assert result is mock_chat_client_class[1]
        await pool.close_all()

    @pytest.mark.asyncio
    async def test_owner_probe_failure_isolated(self, mock_chat_client_class):
        """单条连接 tracks_session 抛错不传染——继续查其它连接。"""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
            _ConnEntry,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=2)
        broken = MagicMock()
        broken.tracks_session = MagicMock(side_effect=RuntimeError("boom"))
        owner = MagicMock()
        owner.tracks_session = MagicMock(return_value=True)
        pool._clients["sbx-4"] = [
            _ConnEntry(client=broken),
            _ConnEntry(client=owner),
        ]

        result = await pool.get("sbx-4", "ws://host/ws", None, session_key="sk-run")
        assert result is owner
        await pool.close_all()


class TestTotalConnections:
    @pytest.mark.asyncio
    async def test_total_connections(self, mock_chat_client_class):
        mock_cls, mock_client = mock_chat_client_class

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool()
        assert pool.total_connections == 0

        await pool.get("sandbox-1", "ws://host/ws")
        assert pool.total_connections == 1

        await pool.get("sandbox-2", "ws://host/ws")
        assert pool.total_connections == 2

        await pool.close_all()
        assert pool.total_connections == 0


class TestHighConcurrency:
    """Verify that high-concurrency get() calls do not create more
    connections than max_conns_per_sandbox allows."""

    @pytest.fixture
    def fake_client_class(self):
        """Patch AsyncChatClient with _FakeChatClient."""
        with patch(
            "secbaas.community.core.service.bot_run._async_chat_client_pool.AsyncChatClient",
            _FakeChatClient,
        ):
            _FakeChatClient.reset_create_count()
            yield
            _FakeChatClient.reset_create_count()

    @pytest.mark.asyncio
    async def test_20_concurrent_max1_creates_exactly_1(self, fake_client_class):
        """20 concurrent coroutines with max_conns_per_sandbox=1 should
        create exactly 1 connection."""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=1)

        async def get_client():
            return await pool.get("sbx-1", "ws://host/ws")

        results = await asyncio.gather(*[get_client() for _ in range(20)])

        conns = pool._clients.get("sbx-1", [])
        assert len(conns) == 1, f"Expected 1 connection, got {len(conns)}"
        assert _FakeChatClient.get_create_count() == 1, (
            f"Expected 1 create call, got {_FakeChatClient.get_create_count()}"
        )
        # All 20 coroutines should share the same client instance
        unique_clients = set(id(r) for r in results)
        assert len(unique_clients) == 1, (
            f"Expected all results to be same client, got {len(unique_clients)} unique"
        )

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_20_concurrent_max2_creates_at_most_2(self, fake_client_class):
        """20 concurrent coroutines with max_conns_per_sandbox=2 should
        create at most 2 connections."""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=2)

        async def get_client():
            return await pool.get("sbx-2", "ws://host/ws")

        results = await asyncio.gather(*[get_client() for _ in range(20)])

        conns = pool._clients.get("sbx-2", [])
        assert len(conns) <= 2, f"Expected <= 2 connections, got {len(conns)}"
        assert _FakeChatClient.get_create_count() <= 2, (
            f"Expected <= 2 create calls, got {_FakeChatClient.get_create_count()}"
        )

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_20_concurrent_max1_busy_creates_exactly_1(self, fake_client_class):
        """20 concurrent coroutines, each marking the connection busy
        immediately after get(), with max=1 should still create exactly 1."""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=1)

        async def get_and_mark_busy():
            client = await pool.get("sbx-busy", "ws://host/ws")
            client._active = 1
            return client

        results = await asyncio.gather(*[get_and_mark_busy() for _ in range(20)])

        conns = pool._clients.get("sbx-busy", [])
        assert len(conns) == 1, f"Expected 1 connection, got {len(conns)}"
        assert _FakeChatClient.get_create_count() == 1, (
            f"Expected 1 create call, got {_FakeChatClient.get_create_count()}"
        )

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_20_concurrent_max1_staggered(self, fake_client_class):
        """20 coroutines with staggered start times (random small delays)
        with max=1 should create exactly 1 connection."""
        import random

        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=1)

        async def get_staggered():
            await asyncio.sleep(random.uniform(0, 0.02))
            return await pool.get("sbx-staggered", "ws://host/ws")

        results = await asyncio.gather(*[get_staggered() for _ in range(20)])

        conns = pool._clients.get("sbx-staggered", [])
        assert len(conns) == 1, f"Expected 1 connection, got {len(conns)}"
        assert _FakeChatClient.get_create_count() == 1, (
            f"Expected 1 create call, got {_FakeChatClient.get_create_count()}"
        )

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_20_concurrent_max3_creates_at_most_3(self, fake_client_class):
        """20 concurrent coroutines with max_conns_per_sandbox=3 should
        create at most 3 connections."""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=3)

        async def get_client():
            return await pool.get("sbx-3", "ws://host/ws")

        results = await asyncio.gather(*[get_client() for _ in range(20)])

        conns = pool._clients.get("sbx-3", [])
        assert len(conns) <= 3, f"Expected <= 3 connections, got {len(conns)}"
        assert _FakeChatClient.get_create_count() <= 3, (
            f"Expected <= 3 create calls, got {_FakeChatClient.get_create_count()}"
        )

        await pool.close_all()

    @pytest.mark.asyncio
    async def test_mixed_sandboxes_isolated(self, fake_client_class):
        """Concurrent calls for different sandbox_ids should not interfere.
        10 coroutines for sbx-A (max=1) + 10 for sbx-B (max=1) should
        create 1 connection per sandbox = 2 total."""
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        pool = AsyncChatClientPool(max_conns_per_sandbox=1)

        async def get_a():
            return await pool.get("sbx-A", "ws://host/ws")

        async def get_b():
            return await pool.get("sbx-B", "ws://host/ws")

        await asyncio.gather(
            *[get_a() for _ in range(10)],
            *[get_b() for _ in range(10)],
        )

        assert len(pool._clients.get("sbx-A", [])) == 1
        assert len(pool._clients.get("sbx-B", [])) == 1
        assert _FakeChatClient.get_create_count() == 2

        await pool.close_all()


class TestReadIgnoreCase:
    """Cover _read_ignore_case branches: resp None, conf_value true/false."""

    def test_none_service_returns_false(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        result = AsyncChatClientPool._read_ignore_case(None)
        assert result is False

    def test_get_config_returns_none(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        svc = MagicMock()
        svc.get_config.return_value = None
        result = AsyncChatClientPool._read_ignore_case(svc)
        assert result is False

    def test_conf_value_true(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        svc = MagicMock()
        svc.get_config.return_value = MagicMock(conf_value="true")
        result = AsyncChatClientPool._read_ignore_case(svc)
        assert result is True

    def test_conf_value_true_with_whitespace(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        svc = MagicMock()
        svc.get_config.return_value = MagicMock(conf_value="  True  ")
        result = AsyncChatClientPool._read_ignore_case(svc)
        assert result is True

    def test_conf_value_false(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        svc = MagicMock()
        svc.get_config.return_value = MagicMock(conf_value="false")
        result = AsyncChatClientPool._read_ignore_case(svc)
        assert result is False

    def test_conf_value_none_in_response(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        svc = MagicMock()
        svc.get_config.return_value = MagicMock(conf_value=None)
        result = AsyncChatClientPool._read_ignore_case(svc)
        assert result is False

    def test_get_config_raises_returns_false(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        svc = MagicMock()
        svc.get_config.side_effect = RuntimeError("db error")
        result = AsyncChatClientPool._read_ignore_case(svc)
        assert result is False


class TestEnableProcessInteraction:
    @pytest.mark.parametrize(
        ("conf_value", "expected"),
        [
            ("true", True),
            ("  True  ", True),
            ("false", False),
            ("invalid", False),
            (None, False),
        ],
    )
    def test_reads_boolean_value(self, conf_value, expected):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )
        from secbaas.community.core.service.config import SystemConfigKey

        service = MagicMock()
        service.get_config.return_value = MagicMock(conf_value=conf_value)

        assert AsyncChatClientPool._enable_process_interaction(service) is expected
        service.get_config.assert_called_once_with(SystemConfigKey.INTERACTION_PROCESS)

    def test_none_service_returns_false(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        assert AsyncChatClientPool._enable_process_interaction(None) is False

    def test_missing_config_returns_false(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        service = MagicMock()
        service.get_config.return_value = None

        assert AsyncChatClientPool._enable_process_interaction(service) is False

    def test_read_failure_returns_false(self):
        from secbaas.community.core.service.bot_run._async_chat_client_pool import (
            AsyncChatClientPool,
        )

        service = MagicMock()
        service.get_config.side_effect = RuntimeError("db unavailable")

        assert AsyncChatClientPool._enable_process_interaction(service) is False
