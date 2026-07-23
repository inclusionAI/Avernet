"""Unit tests for BareCachePlugin — in-memory cache with TTL expiry."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.community.plugins.cache.bare import BareCachePlugin


@pytest.fixture
def cache() -> BareCachePlugin:
    return BareCachePlugin()


class TestCacheGetSet:
    def test_get_missing_key_returns_none(self, cache: BareCachePlugin) -> None:
        assert cache.get("nope") is None

    def test_set_then_get(self, cache: BareCachePlugin) -> None:
        cache.set("key1", "value1", ttl_seconds=60)
        assert cache.get("key1") == "value1"

    def test_overwrite_existing_key(self, cache: BareCachePlugin) -> None:
        cache.set("key", "v1", ttl_seconds=60)
        cache.set("key", "v2", ttl_seconds=60)
        assert cache.get("key") == "v2"

    def test_multiple_keys(self, cache: BareCachePlugin) -> None:
        cache.set("a", "1", ttl_seconds=60)
        cache.set("b", "2", ttl_seconds=60)
        cache.set("c", "3", ttl_seconds=60)
        assert cache.get("a") == "1"
        assert cache.get("b") == "2"
        assert cache.get("c") == "3"

    def test_empty_string_value(self, cache: BareCachePlugin) -> None:
        cache.set("empty", "", ttl_seconds=60)
        assert cache.get("empty") == ""

    def test_unicode_value(self, cache: BareCachePlugin) -> None:
        cache.set("unicode", "你好世界", ttl_seconds=60)
        assert cache.get("unicode") == "你好世界"


class TestCacheTTL:
    def test_not_expired(self, cache: BareCachePlugin) -> None:
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=100.0,
        ):
            cache.set("key", "val", ttl_seconds=30)
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=129.0,
        ):
            assert cache.get("key") == "val"

    def test_expired_returns_none(self, cache: BareCachePlugin) -> None:
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=100.0,
        ):
            cache.set("key", "val", ttl_seconds=30)
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=131.0,
        ):
            assert cache.get("key") is None

    def test_exact_expiry_boundary(self, cache: BareCachePlugin) -> None:
        """expires_at == monotonic() means not yet expired (< comparison is strict)."""
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=100.0,
        ):
            cache.set("key", "val", ttl_seconds=30)
        # expires_at = 100 + 30 = 130; monotonic returns 130; 130 < 130 is False → not expired
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=130.0,
        ):
            assert cache.get("key") == "val"

    def test_expired_entry_removed_from_store(self, cache: BareCachePlugin) -> None:
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=100.0,
        ):
            cache.set("key", "val", ttl_seconds=10)
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=200.0,
        ):
            cache.get("key")
        # Internal store should be empty after expiry-triggered deletion.
        assert len(cache._store) == 0

    def test_zero_ttl_still_set(self, cache: BareCachePlugin) -> None:
        """TTL=0 sets expires_at == monotonic(), so get at same time returns value."""
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=100.0,
        ):
            cache.set("key", "val", ttl_seconds=0)
            assert cache.get("key") == "val"
        # A slightly later read should expire it.
        with patch(
            "gateway.community.plugins.cache.bare._plugin.time.monotonic",
            return_value=100.001,
        ):
            assert cache.get("key") is None


class TestCacheCloseClear:
    def test_close_empties_store(self, cache: BareCachePlugin) -> None:
        cache.set("a", "1", ttl_seconds=60)
        cache.set("b", "2", ttl_seconds=60)
        cache.close()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert len(cache._store) == 0

    def test_clear_empties_store(self, cache: BareCachePlugin) -> None:
        cache.set("a", "1", ttl_seconds=60)
        cache.clear()
        assert cache.get("a") is None
        assert len(cache._store) == 0

    def test_close_then_set_works(self, cache: BareCachePlugin) -> None:
        cache.close()
        cache.set("after", "close", ttl_seconds=60)
        assert cache.get("after") == "close"

    def test_clear_then_set_works(self, cache: BareCachePlugin) -> None:
        cache.clear()
        cache.set("after", "clear", ttl_seconds=60)
        assert cache.get("after") == "clear"

    def test_close_idempotent(self, cache: BareCachePlugin) -> None:
        cache.close()
        cache.close()
        assert len(cache._store) == 0
