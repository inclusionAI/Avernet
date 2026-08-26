"""Unit tests for RedisCachePlugin.

Mocks the Redis client to avoid requiring a live Redis server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.plugins.cache.redis import RedisCachePlugin


@pytest.fixture
def plugin() -> RedisCachePlugin:
    """Create a RedisCachePlugin with a mocked Redis client."""
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    with patch(
        "secbaas.community.plugins.cache.redis._redis_cache.Redis.from_url",
        return_value=mock_redis,
    ):
        p = RedisCachePlugin("redis://localhost:6379/0")
    return p


class TestRedisCachePlugin:
    def test_get_missing_key_returns_none(self, plugin: RedisCachePlugin) -> None:
        plugin._redis.get.return_value = None
        assert plugin.get("nope") is None

    def test_set_then_get(self, plugin: RedisCachePlugin) -> None:
        plugin._redis.get.return_value = "bar"
        plugin.set("foo", "bar", ttl_seconds=60)
        plugin._redis.set.assert_called_once_with("foo", "bar", ex=60)
        assert plugin.get("foo") == "bar"

    def test_set_calls_redis_with_ttl(self, plugin: RedisCachePlugin) -> None:
        plugin.set("key", "value", ttl_seconds=120)
        plugin._redis.set.assert_called_once_with("key", "value", ex=120)

    def test_get_returns_stored_value(self, plugin: RedisCachePlugin) -> None:
        plugin._redis.get.return_value = "hello"
        assert plugin.get("key") == "hello"
        plugin._redis.get.assert_called_once_with("key")

    def test_close_calls_redis_close(self, plugin: RedisCachePlugin) -> None:
        plugin.close()
        plugin._redis.close.assert_called_once()
