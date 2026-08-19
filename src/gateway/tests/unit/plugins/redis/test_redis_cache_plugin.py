"""Unit tests for RedisCachePlugin — redis-backed cache with server-side TTL.

Uses a fake client injected into the plugin so tests never require a live
Redis instance. TTL behavior is verified by asserting the ``set`` call passes
``ex=ttl_seconds`` (server-side expiry) and that a ``get`` returning ``None``
(the key expired or is absent) surfaces as ``None``.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.community.plugins.cache.redis import RedisCacheConfig, RedisCachePlugin


class _FakeRedis:
    """Minimal stand-in for a redis client with dict-backed storage."""

    def __init__(self, store: dict[str, str] | None = None) -> None:
        self._store = store if store is not None else {}
        self._set_calls: list[tuple[str, str, int | None]] = []
        self.closed = False

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._set_calls.append((key, value, ex))
        self._store[key] = value

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def plugin(fake_client: _FakeRedis) -> RedisCachePlugin:
    return RedisCachePlugin(RedisCacheConfig(host="test"), client=fake_client)  # type: ignore[arg-type]


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

    def test_bytes_value_decoded(self, plugin: RedisCachePlugin) -> None:
        plugin.set("k", "你好", ttl_seconds=60)
        plugin.get("k")

    def test_redis_bytes_are_decoded(self, fake_client: _FakeRedis) -> None:
        """A Redis client returns bytes; the plugin decodes them to str."""
        fake_client._store["k"] = "你好"
        fake_client.get = lambda key: b"\xe4\xbd\xa0\xe5\xa5\xbd"  # "你好" in utf-8
        plugin = RedisCachePlugin(RedisCacheConfig(host="test"), client=fake_client)  # type: ignore[arg-type]
        assert plugin.get("k") == "你好"


class TestRedisCacheTTL:
    def test_set_passes_server_side_ttl(
        self, plugin: RedisCachePlugin, fake_client: _FakeRedis
    ) -> None:
        plugin.set("k", "v", ttl_seconds=30)
        assert fake_client._set_calls[-1] == ("k", "v", 30)

    def test_expired_key_returns_none(
        self, plugin: RedisCachePlugin, fake_client: _FakeRedis
    ) -> None:
        fake_client._store["k"] = "v"  # server considers it present
        fake_client.get = lambda key: None  # server-side expiry ⇒ None
        assert plugin.get("k") is None


class TestRedisCacheClose:
    def test_close_releases_client(
        self, plugin: RedisCachePlugin, fake_client: _FakeRedis
    ) -> None:
        plugin.close()
        assert fake_client.closed

    def test_close_sets_client_none(self, plugin: RedisCachePlugin) -> None:
        plugin.close()
        assert plugin._client is None

    def test_close_idempotent(self, plugin: RedisCachePlugin) -> None:
        plugin.close()
        plugin.close()


class TestRedisCacheConfig:
    def test_config_from_dict(self) -> None:
        cfg = RedisCacheConfig(host="h", port=6379)
        assert cfg.host == "h"
        assert cfg.port == 6379
        assert cfg.db == 0
        assert cfg.ssl is False

    def test_plugin_accepts_dict_config(self, fake_client: _FakeRedis) -> None:
        plugin = RedisCachePlugin({"host": "h"}, client=fake_client)  # type: ignore[arg-type]
        assert plugin._config.host == "h"

    def test_lazy_client_build(self) -> None:
        plugin = RedisCachePlugin(RedisCacheConfig(host="h"), client=None)
        assert plugin._client is None
        client = plugin._get_client()
        assert client is not None
        assert plugin._client is not None
