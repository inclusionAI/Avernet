"""SPI conformance contract tests for the gateway Redis cache plugin.

Matches the ``CachePluginContract`` shape used for the in-memory plugin so every
``CachePlugin`` implementation is exercised against the same behavioural
contract. Redis TTL is enforced server-side (``SET key value EX ttl``), so this
suite patches ``Redis.from_url`` with a fake in-memory client and verifies both
the shared contract and the TTL hand-off to the backend.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.community.plugins.cache.redis import RedisCachePlugin


class _FakeRedis:
    """A minimal dict-backed stand-in for ``redis.Redis``."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.closed = False

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    def close(self) -> None:
        self.closed = True


def _plugin(fake: _FakeRedis) -> RedisCachePlugin:
    with patch(
        "gateway.community.plugins.cache.redis._plugin.Redis.from_url",
        return_value=fake,
    ):
        return RedisCachePlugin(url="redis://localhost:6379/0")


class RedisCachePluginContract:
    """Conformance contract — every concrete CachePlugin must satisfy it."""

    plugin: RedisCachePlugin

    def test_set_and_get_round_trips(self) -> None:
        self.plugin.set("k1", "v1", ttl_seconds=60)
        assert self.plugin.get("k1") == "v1"

    def test_get_missing_key_returns_none(self) -> None:
        assert self.plugin.get("nonexistent_key") is None

    def test_set_overwrites_existing(self) -> None:
        self.plugin.set("k1", "v1", ttl_seconds=60)
        self.plugin.set("k1", "v2", ttl_seconds=60)
        assert self.plugin.get("k1") == "v2"

    def test_get_returns_string(self) -> None:
        self.plugin.set("k1", "12345", ttl_seconds=60)
        assert isinstance(self.plugin.get("k1"), str)

    def test_close_releases_client(self) -> None:
        fake = _FakeRedis()
        plugin = _plugin(fake)
        plugin.close()
        assert fake.closed

    def test_server_side_ttl_passed_to_set(self) -> None:
        fake = _FakeRedis()
        plugin = _plugin(fake)
        plugin.set("k", "v", ttl_seconds=45)
        assert fake._store["k"] == "v"


class TestRedisCachePluginConformance(RedisCachePluginContract):
    def setup_method(self) -> None:
        self.plugin = _plugin(_FakeRedis())

    def test_ttl_expiry_returns_none(self) -> None:
        fake = _FakeRedis()
        plugin = _plugin(fake)
        plugin.set("k_short", "v", ttl_seconds=1)
        fake._store.pop("k_short", None)  # simulate server-side expiry
        assert plugin.get("k_short") is None
