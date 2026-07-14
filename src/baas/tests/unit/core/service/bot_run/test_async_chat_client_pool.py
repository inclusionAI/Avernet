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
from unittest.mock import AsyncMock, MagicMock, patch

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
            ignore_case=False,
        )

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
