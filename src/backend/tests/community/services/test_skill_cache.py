"""Tests for core/services/skill_cache.py - MarketCache and GlobalSyncLock"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.skill_center.services.skill_cache import MarketCache, GlobalSyncLock


# ============================================================================
# Helper: create a fresh MarketCache that always falls back to memory
# ============================================================================

def _make_memory_only_cache() -> MarketCache:
    """Return a MarketCache instance forced into memory-only mode."""
    import time as _time
    cache = MarketCache(cache_plugin=MagicMock())
    cache._zcache_available = False  # skip ZCache entirely
    # Pin the probe far in the future so the TTL re-check never re-probes and
    # flips us back to "available" mid-test.
    cache._zcache_checked_at = _time.time() + 1e9
    return cache


# ============================================================================
# TestMarketCache
# ============================================================================

class TestMarketCache:

    def test_get_returns_none_for_missing_key(self):
        cache = _make_memory_only_cache()
        assert cache.get("nonexistent_key") is None

    def test_set_and_get(self):
        cache = _make_memory_only_cache()
        data = {"skills": [{"id": "s1", "name": "demo"}]}
        cache.set("my_key", data)
        result = cache.get("my_key")
        assert result == data

    def test_invalidate_clears_all(self):
        cache = _make_memory_only_cache()
        cache.set("key_a", {"a": 1})
        cache.set("key_b", {"b": 2})
        # both should be readable
        assert cache.get("key_a") is not None
        assert cache.get("key_b") is not None

        cache.invalidate()

        assert cache.get("key_a") is None
        assert cache.get("key_b") is None

    def test_set_overwrite(self):
        cache = _make_memory_only_cache()
        cache.set("k", {"v": 1})
        cache.set("k", {"v": 2})
        assert cache.get("k") == {"v": 2}


# ============================================================================
# TestGlobalSyncLock
# ============================================================================

class TestGlobalSyncLock:

    def setup_method(self):
        """Reset global state before each test."""
        # Release any held lock and reset sync time
        GlobalSyncLock.release()
        # Reset last_sync_time to 0 via internal state
        import agentclaw.community.core.skill_center.services.skill_cache as mod
        mod._global_sync_state["last_sync_time"] = 0

    def test_acquire_and_release(self):
        assert GlobalSyncLock.acquire() is True
        # While held, a second acquire should fail
        assert GlobalSyncLock.acquire() is False
        GlobalSyncLock.release()
        # After release, acquire should succeed again
        assert GlobalSyncLock.acquire() is True
        GlobalSyncLock.release()

    def test_rate_limit(self):
        # Initially no sync recorded, should be allowed
        allowed, remaining = GlobalSyncLock.check_rate_limit(min_interval=60)
        assert allowed is True
        assert remaining == 0

        # Record a sync
        GlobalSyncLock.record_sync()

        # Immediately check — should be rate-limited
        allowed, remaining = GlobalSyncLock.check_rate_limit(min_interval=60)
        assert allowed is False
        assert remaining > 0
