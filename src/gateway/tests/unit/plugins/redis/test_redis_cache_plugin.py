"""Unit tests for RedisCachePlugin — redis-backed cache with server-side TTL.

The plugin connects eagerly (``Redis.from_url`` + ``ping``) and decodes
responses to ``str``. ``Redis.from_url`` is patched to return a fake client so
tests never require a live Redis instance. TTL behavior is verified by asserting
the ``set`` call passes ``ex=ttl_seconds`` (server-side expiry).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.community.plugins.cache.redis import RedisCachePlugin


class _FakeRedisClient:
    """Stand-in for the ``redis.Redis`` client built by ``Redis.from_url``."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._set_calls: list[tuple[str, str, int | None]] = []
        self.closed = False
        self.ping_called = False

    def ping(self) -> bool:
        self.ping_called = True
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._set_calls.append((key, value, ex))
        self._store[key] = value

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def client() -> _FakeRedisClient:
    return _FakeRedisClient()


@pytest.fixture
def from_url(client: _FakeRedisClient):
    with patch(
        "gateway.community.plugins.cache.redis._plugin.Redis.from_url",
        return_value=client,
    ) as mock:
        yield mock


@pytest.fixture
def plugin(from_url) -> RedisCachePlugin:
    return RedisCachePlugin(url="redis://localhost:6379/0")


class TestRedisCacheGetSet:
    def test_set_then_get(self, plugin: RedisCachePlugin) -> None:
        plugin.set("key1", "value1", ttl_seconds=60)
        assert plugin.get("key1") == "value1"

    def test_get_missing_key_returns_none(self, plugin: RedisCachePlugin) -> None:
        assert plugin.get("nonexistent_key") is None

    def test_set_overwrites_existing(self, plugin: RedisCachePlugin) -> None:
        plugin.set("k", "v1", ttl_seconds=60)
        plugin.set("k", "v2", ttl_seconds=60)
        assert plugin.get("k") == "v2"

    def test_get_returns_string(self, plugin: RedisCachePlugin) -> None:
        plugin.set("k", "12345", ttl_seconds=60)
        assert isinstance(plugin.get("k"), str)

    def test_empty_string_value(self, plugin: RedisCachePlugin) -> None:
        plugin.set("k", "", ttl_seconds=60)
        assert plugin.get("k") == ""


class TestRedisCacheTTL:
    def test_set_passes_server_side_ttl(
        self, plugin: RedisCachePlugin, client: _FakeRedisClient
    ) -> None:
        plugin.set("k", "v", ttl_seconds=30)
        assert client._set_calls[-1] == ("k", "v", 30)

    def test_expired_key_returns_none(self, client: _FakeRedisClient) -> None:
        client.get = lambda key: None  # server-side expiry ⇒ None
        with patch(
            "gateway.community.plugins.cache.redis._plugin.Redis.from_url",
            return_value=client,
        ):
            plugin = RedisCachePlugin(url="redis://localhost:6379/0")
        assert plugin.get("k") is None


class TestRedisCacheClose:
    def test_close_releases_client(
        self, plugin: RedisCachePlugin, client: _FakeRedisClient
    ) -> None:
        plugin.close()
        assert client.closed

    def test_close_idempotent(self, plugin: RedisCachePlugin) -> None:
        plugin.close()
        plugin.close()


class TestRedisCacheConstruction:
    def test_connects_eagerly(self, client: _FakeRedisClient) -> None:
        with patch(
            "gateway.community.plugins.cache.redis._plugin.Redis.from_url",
            return_value=client,
        ):
            RedisCachePlugin(url="redis://localhost:6379/0")
        assert client.ping_called

    def test_passes_socket_timeouts(self, from_url) -> None:
        RedisCachePlugin(
            url="redis://localhost:6379/0",
            socket_timeout=3.0,
            socket_connect_timeout=7.0,
        )
        from_url.assert_called_once_with(
            "redis://localhost:6379/0",
            socket_timeout=3.0,
            socket_connect_timeout=7.0,
            decode_responses=True,
        )

    def test_connection_error_raises_at_startup(self) -> None:
        from types import MethodType

        from redis.exceptions import ConnectionError as RedisConnectionError

        failing = _FakeRedisClient()

        def _raise_ping(self) -> bool:
            raise RedisConnectionError("down")

        failing.ping = MethodType(_raise_ping, failing)  # type: ignore[method-assign]
        with patch(
            "gateway.community.plugins.cache.redis._plugin.Redis.from_url",
            return_value=failing,
        ):
            with pytest.raises(RuntimeError, match="Cannot connect to Redis"):
                RedisCachePlugin(url="redis://localhost:6379/0")
