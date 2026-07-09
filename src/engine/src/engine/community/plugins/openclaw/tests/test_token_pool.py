"""Port-impl tests for plugins/community/openclaw/token_pool.TokenClientPool.

Covers the pool's refcount / lifecycle behaviour with the
OpenClawGatewayClient constructor mocked so no actual WebSocket connections
are attempted.

Migrated from engines/openclaw/tests/test_token_pool.py; the only API
change is that the new pool takes ``token: str | None`` directly instead of
``AuthContext``.  All coverage is preserved.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.community.config import MCPTokenSettings
from engine.community.plugins.openclaw.token_pool import TokenClientPool


def _mock_client_factory() -> tuple[MagicMock, MagicMock]:
    """Return (mock_cls, mock_instance) for patching OpenClawGatewayClient.

    Each call to the class constructor yields the same mock instance so tests
    can assert how many times the pool actually built a fresh client.
    """
    instance = MagicMock(name="OpenClawGatewayClient-instance")
    instance.connected = False

    async def _connect():
        instance.connected = True

    async def _disconnect():
        instance.connected = False

    instance.connect = AsyncMock(side_effect=_connect)
    instance.disconnect = AsyncMock(side_effect=_disconnect)

    cls = MagicMock(return_value=instance)
    return cls, instance


def _forwarding_settings() -> MCPTokenSettings:
    """Settings where token forwarding is on — get() will route per-token."""
    return MCPTokenSettings(
        header_name="x-test-token",
        forward_to_wss=True,
        persist_enabled=False,
        store_path=None,
    )


def _no_forward_settings() -> MCPTokenSettings:
    """Settings with forwarding disabled — get() falls back to default."""
    return MCPTokenSettings(
        header_name="x-test-token",
        forward_to_wss=False,
        persist_enabled=False,
        store_path=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# register / release refcount
# ─────────────────────────────────────────────────────────────────────────────


class TestRegisterRelease:
    def test_register_with_none_is_noop(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        pool.register(None)
        assert pool._refcount == {}

    @pytest.mark.asyncio
    async def test_release_with_none_is_noop(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        await pool.release(None)
        assert pool._refcount == {}

    def test_register_increments_refcount(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        pool.register("t1")
        pool.register("t1")
        pool.register("t2")
        assert pool._refcount == {"t1": 2, "t2": 1}

    @pytest.mark.asyncio
    async def test_release_decrements_and_teardown_at_zero(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        token = "tok-a"

        pool.register(token)
        pool.register(token)
        await pool.release(token)
        assert pool._refcount == {"tok-a": 1}

        await pool.release(token)
        # Count hit zero — all per-token state cleared.
        assert "tok-a" not in pool._refcount
        assert "tok-a" not in pool._clients
        assert "tok-a" not in pool._locks


# ─────────────────────────────────────────────────────────────────────────────
# get() routing
# ─────────────────────────────────────────────────────────────────────────────


class TestGet:
    @pytest.mark.asyncio
    async def test_none_token_uses_default_singleton(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        default = MagicMock(name="default-client")
        with patch(
            "engine.community.plugins.openclaw.token_pool.get_client",
            new=AsyncMock(return_value=default),
        ):
            c1 = await pool.get(None)
            c2 = await pool.get(None)
        assert c1 is default
        assert c2 is default
        assert pool._clients == {}  # no per-token clients created

    @pytest.mark.asyncio
    async def test_forwarding_disabled_uses_default_even_with_token(self):
        pool = TokenClientPool(settings=_no_forward_settings())
        default = MagicMock(name="default-client")
        with patch(
            "engine.community.plugins.openclaw.token_pool.get_client",
            new=AsyncMock(return_value=default),
        ):
            c = await pool.get("tok-a")
        assert c is default
        assert pool._clients == {}

    @pytest.mark.asyncio
    async def test_token_creates_and_connects_per_token_client(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        cls, instance = _mock_client_factory()
        with patch(
            "engine.community.plugins.openclaw.token_pool.OpenClawGatewayClient", cls,
        ):
            c = await pool.get("tok-a")
        assert c is instance
        cls.assert_called_once_with(upstream_headers={"x-test-token": "tok-a"})
        instance.connect.assert_awaited_once()
        assert pool._clients == {"tok-a": instance}

    @pytest.mark.asyncio
    async def test_second_get_reuses_existing_client(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        cls, instance = _mock_client_factory()
        with patch(
            "engine.community.plugins.openclaw.token_pool.OpenClawGatewayClient", cls,
        ):
            c1 = await pool.get("tok-a")
            c2 = await pool.get("tok-a")
        assert c1 is c2
        cls.assert_called_once()
        instance.connect.assert_awaited_once()  # already connected on second get

    @pytest.mark.asyncio
    async def test_distinct_tokens_get_distinct_clients(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        instances = [MagicMock(name=f"c{i}") for i in range(2)]
        for inst in instances:
            inst.connected = False
            inst.connect = AsyncMock()
        cls = MagicMock(side_effect=instances)
        with patch(
            "engine.community.plugins.openclaw.token_pool.OpenClawGatewayClient", cls,
        ):
            c_a = await pool.get("tok-a")
            c_b = await pool.get("tok-b")
        assert c_a is not c_b
        assert c_a is instances[0]
        assert c_b is instances[1]
        assert set(pool._clients) == {"tok-a", "tok-b"}


# ─────────────────────────────────────────────────────────────────────────────
# release() + get() integration
# ─────────────────────────────────────────────────────────────────────────────


class TestReleaseDisconnects:
    @pytest.mark.asyncio
    async def test_release_on_zero_disconnects_client(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        cls, instance = _mock_client_factory()
        with patch(
            "engine.community.plugins.openclaw.token_pool.OpenClawGatewayClient", cls,
        ):
            token = "tok-a"
            pool.register(token)
            await pool.get(token)
            assert pool._clients == {"tok-a": instance}
            await pool.release(token)
        instance.disconnect.assert_awaited_once()
        assert pool._clients == {}

    @pytest.mark.asyncio
    async def test_release_above_zero_does_not_disconnect(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        cls, instance = _mock_client_factory()
        with patch(
            "engine.community.plugins.openclaw.token_pool.OpenClawGatewayClient", cls,
        ):
            token = "tok-a"
            pool.register(token)
            pool.register(token)
            await pool.get(token)
            await pool.release(token)
        instance.disconnect.assert_not_awaited()
        assert pool._clients == {"tok-a": instance}

    @pytest.mark.asyncio
    async def test_release_swallows_disconnect_exception(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        cls, instance = _mock_client_factory()
        instance.disconnect = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "engine.community.plugins.openclaw.token_pool.OpenClawGatewayClient", cls,
        ):
            token = "tok-a"
            pool.register(token)
            await pool.get(token)
            # Should not raise despite disconnect failing.
            await pool.release(token)
        assert pool._clients == {}


# ─────────────────────────────────────────────────────────────────────────────
# shutdown
# ─────────────────────────────────────────────────────────────────────────────


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_disconnects_every_tracked_client(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        instances = [MagicMock(name=f"c{i}") for i in range(3)]
        for inst in instances:
            inst.connected = False
            inst.connect = AsyncMock()
            inst.disconnect = AsyncMock()
        cls = MagicMock(side_effect=instances)
        with patch(
            "engine.community.plugins.openclaw.token_pool.OpenClawGatewayClient", cls,
        ):
            await pool.get("t1")
            await pool.get("t2")
            await pool.get("t3")

        await pool.shutdown()
        for inst in instances:
            inst.disconnect.assert_awaited_once()
        assert pool._clients == {}
        assert pool._refcount == {}
        assert pool._locks == {}


# ─────────────────────────────────────────────────────────────────────────────
# Concurrency — concurrent get() for same token returns the same client
# ─────────────────────────────────────────────────────────────────────────────


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_get_same_token_serialized(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        cls, instance = _mock_client_factory()
        with patch(
            "engine.community.plugins.openclaw.token_pool.OpenClawGatewayClient", cls,
        ):
            results = await asyncio.gather(
                pool.get("t"), pool.get("t"), pool.get("t"),
            )
        assert all(r is instance for r in results)
        # Despite concurrent get()s, the constructor ran exactly once.
        cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_swallows_disconnect_exception(self):
        pool = TokenClientPool(settings=_forwarding_settings())
        bad = MagicMock(name="bad-client")
        bad.connected = True
        bad.disconnect = AsyncMock(side_effect=RuntimeError("boom"))
        good = MagicMock(name="good-client")
        good.connected = True
        good.disconnect = AsyncMock()
        pool._clients = {"bad": bad, "good": good}
        pool._locks = {"bad": asyncio.Lock(), "good": asyncio.Lock()}
        pool._refcount = {"bad": 1, "good": 1}

        await pool.shutdown()

        bad.disconnect.assert_awaited_once()
        good.disconnect.assert_awaited_once()
        assert pool._clients == {}
        assert pool._locks == {}
        assert pool._refcount == {}
