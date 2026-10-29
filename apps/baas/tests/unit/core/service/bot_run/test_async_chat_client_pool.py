"""Unit tests for AsyncChatClientPool.

Covers:
- Constructor defaults and custom params
- Parameter passthrough to AsyncChatClient
- _pick_idle skips reconnecting connections
- _pick_least_sessions skips reconnecting connections
- _remove_unhealthy skips reconnecting connections
- High-concurrency connection creation limits
"""

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_chat_client():
    """Create a mock AsyncChatClient."""
    client = MagicMock()
    client.is_connected = True
    client.is_reconnecting = False
    client.has_active_sessions = False
    client.active_session_count = 0
    client.connect = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_chat_client_class(mock_chat_client):
    """Patch AsyncChatClient to return our mock."""
    with patch(
        "secbaas.community.core.service.bot_run._async_chat_client_pool.AsyncChatClient",
        return_value=mock_chat_client,
    ) as mock_cls:
        yield mock_cls, mock_chat_client


# ==================== constructor tests ====================


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


# ==================== param passthrough tests ====================


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
            a2a_mode="off",
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


# ==================== reconnect awareness tests ====================


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


# ==================== R4-1: session 归属定向 get ====================


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


# ==================== total_connections tests ====================


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


# ==================== high concurrency tests ====================


class _FakeChatClient:
    """Fake AsyncChatClient for concurrency tests.

    Tracks creation count and simulates connect latency.
    active_session_count is controllable per-instance.
    """

    _create_count = 0

    @classmethod
    def reset_create_count(cls):
        cls._create_count = 0

    @classmethod
    def get_create_count(cls):
        return cls._create_count

    def __init__(self, **kwargs):
        _FakeChatClient._create_count += 1
        self._active = 0
        self._connected = False
        self.is_reconnecting = False
        self.a2a_mode = kwargs.get("a2a_mode", "off")

    @property
    def is_connected(self):
        return self._connected

    @property
    def active_session_count(self):
        return self._active

    @property
    def has_active_sessions(self):
        return self._active > 0

    async def connect(self):
        await asyncio.sleep(0.005)
        self._connected = True

    async def close(self):
        self._connected = False


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


# ==================== _read_ignore_case tests ====================


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


# ==================== interaction processing flag tests ====================


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


# ==================== P1-3: mode flip reuse validation tests ====================


def _mode_flip_config_service(holder: dict):
    """可翻转的 SystemConfigManageService：每次 get_config 都读 holder 当前值。"""
    service = MagicMock()

    def _get(conf_key):
        resp = MagicMock()
        resp.conf_value = holder.get("value", "off")
        return resp

    service.get_config.side_effect = _get
    return service


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
        assert c1.a2a_mode == "off"

        holder["value"] = "grey"
        await asyncio.sleep(0)  # holder 翻转（side_effect 每调用现读）
        c2 = await pool.get("sbx-flip", "ws://host/ws")
        assert c2 is not c1, "grey 开启后不得继续复用 off 存量连接"
        assert c2.a2a_mode == "grey"
        await asyncio.sleep(0.01)
        assert c1._connected is False, "空闲 off 连接在 grey 翻转后应立即关闭"

        holder["value"] = "off"
        c3 = await pool.get("sbx-flip", "ws://host/ws")
        assert c3 is not c2, "off 回退后不得继续复用 grey 存量连接"
        assert c3.a2a_mode == "off"
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
        assert c2.a2a_mode == "grey"
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


# ==================== _read_a2a_mode failure tests ====================


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

        assert mock_cls.call_args.kwargs["a2a_mode"] == "off"
        await pool.close_all()


# ==================== sensitive bypass keys loader tests ====================


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


# ==================== outbound auth refresh（120s JWT 快照过期正修） ====================


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
