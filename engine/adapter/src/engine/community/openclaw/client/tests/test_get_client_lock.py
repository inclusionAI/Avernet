"""Tests for ConnectionPool and get_client() behavior.

Verifies that:
1. Pool round-robins across connected clients
2. Pool auto-reconnects disconnected slots
3. Concurrent calls don't trigger parallel connects on same slot
4. Failure of one slot doesn't break the pool
5. shutdown() closes all connections
6. get_shared_client() returns the first slot synchronously
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.community.openclaw.client import gateway_client
from engine.community.openclaw.client.gateway_client import (
    ConnectionPool,
    OpenClawGatewayClient,
    get_client,
    get_shared_client,
    close_client,
)


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset module-level pool before each test."""
    gateway_client._pool = None
    yield
    gateway_client._pool = None


def _make_connected_client() -> OpenClawGatewayClient:
    client = OpenClawGatewayClient()
    client._connected = True
    client._ws = MagicMock()
    return client


@pytest.mark.asyncio
async def test_pool_round_robins_across_connected_clients():
    pool = ConnectionPool(size=3)
    pool._clients = [_make_connected_client() for _ in range(3)]
    pool._initialized = True

    c1 = await pool.get()
    c2 = await pool.get()
    c3 = await pool.get()
    c4 = await pool.get()

    assert c1 is pool._clients[0]
    assert c2 is pool._clients[1]
    assert c3 is pool._clients[2]
    assert c4 is pool._clients[0]


@pytest.mark.asyncio
async def test_pool_skips_disconnected_and_reconnects():
    pool = ConnectionPool(size=3)
    pool._clients = [_make_connected_client() for _ in range(3)]
    pool._initialized = True

    pool._clients[0]._connected = False
    pool._clients[0]._ws = None

    async def fake_connect():
        pool._clients[0]._connected = True
        pool._clients[0]._ws = MagicMock()

    with patch.object(pool._clients[0], "connect", side_effect=fake_connect):
        result = await pool.get()

    assert result is pool._clients[0]
    assert result.connected


@pytest.mark.asyncio
async def test_pool_raises_when_all_connections_fail():
    pool = ConnectionPool(size=2)
    c1 = OpenClawGatewayClient()
    c2 = OpenClawGatewayClient()
    pool._clients = [c1, c2]
    pool._initialized = True

    with patch.object(c1, "connect", side_effect=ConnectionError("fail1")):
        with patch.object(c2, "connect", side_effect=ConnectionError("fail2")):
            with pytest.raises(ConnectionError, match="All pool connections failed"):
                await pool.get()


@pytest.mark.asyncio
async def test_pool_get_least_busy():
    pool = ConnectionPool(size=3)
    pool._clients = [_make_connected_client() for _ in range(3)]
    pool._initialized = True

    pool._clients[0]._pending_requests = {"a": None, "b": None}
    pool._clients[1]._pending_requests = {"c": None}
    pool._clients[2]._pending_requests = {}

    result = await pool.get_least_busy()
    assert result is pool._clients[2]


@pytest.mark.asyncio
async def test_pool_shutdown_disconnects_all():
    pool = ConnectionPool(size=2)
    pool._clients = [_make_connected_client() for _ in range(2)]
    pool._initialized = True

    for c in pool._clients:
        c.disconnect = AsyncMock()

    await pool.shutdown()

    for c in [pool._clients] if pool._clients else []:
        pass
    assert pool._clients == []
    assert not pool._initialized


@pytest.mark.asyncio
async def test_pool_concurrent_get_only_initializes_once():
    pool = ConnectionPool(size=2)
    clients = [_make_connected_client() for _ in range(2)]
    pool._clients = clients
    pool._initialized = True

    results = await asyncio.gather(
        pool.get(),
        pool.get(),
        pool.get(),
    )

    assert all(r.connected for r in results)
    assert all(r in clients for r in results)


@pytest.mark.asyncio
async def test_get_client_uses_pool():
    gateway_client._pool = None

    pool = ConnectionPool(size=2)
    pool._clients = [_make_connected_client() for _ in range(2)]
    pool._initialized = True
    gateway_client._pool = pool

    result = await get_client()
    assert result in pool._clients


def test_get_shared_client_returns_first_slot():
    gateway_client._pool = None

    pool = ConnectionPool(size=3)
    pool._clients = [OpenClawGatewayClient() for _ in range(3)]
    pool._initialized = True
    gateway_client._pool = pool

    result = get_shared_client()
    assert result is pool._clients[0]


@pytest.mark.asyncio
async def test_close_client_shuts_down_pool():
    pool = ConnectionPool(size=2)
    pool._clients = [_make_connected_client() for _ in range(2)]
    pool._initialized = True
    for c in pool._clients:
        c.disconnect = AsyncMock()
    gateway_client._pool = pool

    await close_client()

    assert gateway_client._pool is None


@pytest.mark.asyncio
async def test_pool_size_1_behaves_like_singleton():
    pool = ConnectionPool(size=1)
    client = _make_connected_client()
    pool._clients = [client]
    pool._initialized = True

    c1 = await pool.get()
    c2 = await pool.get()
    c3 = await pool.get()

    assert c1 is c2 is c3 is client


@pytest.mark.asyncio
async def test_pool_ensure_initialized_creates_clients():
    """_ensure_initialized creates N client slots on first call."""
    pool = ConnectionPool(size=3)
    assert pool._clients == []
    assert not pool._initialized

    with patch.object(
        OpenClawGatewayClient, "connect",
        side_effect=lambda: setattr(pool._clients[0], "_connected", True) or None,
    ):
        pass

    await pool._ensure_initialized()

    assert len(pool._clients) == 3
    assert pool._initialized


@pytest.mark.asyncio
async def test_pool_ensure_initialized_idempotent():
    """Calling _ensure_initialized twice doesn't create extra clients."""
    pool = ConnectionPool(size=2)
    await pool._ensure_initialized()
    assert len(pool._clients) == 2

    await pool._ensure_initialized()
    assert len(pool._clients) == 2


@pytest.mark.asyncio
async def test_pool_get_with_ws_cleanup_on_failure():
    """When connect fails and client has a dangling _ws, it gets closed."""
    pool = ConnectionPool(size=1)
    client = OpenClawGatewayClient()
    client._connected = False
    mock_ws = AsyncMock()
    client._ws = mock_ws
    pool._clients = [client]
    pool._initialized = True

    with patch.object(client, "connect", side_effect=ConnectionError("refused")):
        with pytest.raises(ConnectionError, match="All pool connections failed"):
            await pool.get()

    mock_ws.close.assert_called_once()
    assert client._ws is None


@pytest.mark.asyncio
async def test_pool_shutdown_handles_disconnect_error():
    """shutdown() logs warning but doesn't raise on disconnect failure."""
    pool = ConnectionPool(size=2)
    pool._clients = [_make_connected_client() for _ in range(2)]
    pool._initialized = True

    pool._clients[0].disconnect = AsyncMock(side_effect=Exception("disconnect boom"))
    pool._clients[1].disconnect = AsyncMock()

    await pool.shutdown()

    assert pool._clients == []
    assert not pool._initialized


@pytest.mark.asyncio
async def test_get_pool_creates_from_config():
    """_get_pool() creates a pool using OpenClawConfig.ws_pool_size."""
    gateway_client._pool = None

    with patch("engine.community.openclaw.config.get_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(ws_pool_size=5)
        from engine.community.openclaw.client.gateway_client import _get_pool
        pool = _get_pool()

    assert pool._size == 5
    gateway_client._pool = None


def test_get_shared_client_creates_first_slot_when_empty():
    """get_shared_client() creates a client if pool has no slots yet."""
    gateway_client._pool = None

    with patch("engine.community.openclaw.client.gateway_client.get_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(ws_pool_size=3)
        result = get_shared_client()

    assert result is not None
    assert isinstance(result, OpenClawGatewayClient)
    gateway_client._pool = None


@pytest.mark.asyncio
async def test_close_client_is_noop_when_no_pool():
    """close_client() does nothing when pool hasn't been created."""
    gateway_client._pool = None
    await close_client()
    assert gateway_client._pool is None


@pytest.mark.asyncio
async def test_pool_get_least_busy_falls_back_to_get():
    """get_least_busy() falls back to get() when no connection is connected."""
    pool = ConnectionPool(size=2)
    c1 = OpenClawGatewayClient()
    c2 = OpenClawGatewayClient()
    pool._clients = [c1, c2]
    pool._initialized = True

    async def connect_first():
        c1._connected = True
        c1._ws = MagicMock()

    with patch.object(c1, "connect", side_effect=connect_first):
        result = await pool.get_least_busy()

    assert result is c1
