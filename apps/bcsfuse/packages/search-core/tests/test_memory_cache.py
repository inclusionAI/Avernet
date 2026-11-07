"""Bounded caches expire, evict and cannot be refilled after retirement."""

from search_core.core.memory_cache import MemoryCache


def test_expiry_lru_budget_and_retirement():
    now = [0.0]
    cache = MemoryCache(
        enabled=True, ttl_ms=100, max_entries=2, max_bytes=2000, clock=lambda: now[0]
    )
    cache.put(b"a", "first")
    cache.put(b"b", "second")
    assert cache.get(b"a") == "first"
    cache.put(b"c", "third")
    assert cache.get(b"b") is None
    now[0] = 0.1
    assert cache.get(b"a") is None
    cache.put(b"big", "x" * 3000)
    assert cache.get(b"big") is None
    cache.retire()
    cache.put(b"late", "old in-flight request")
    assert cache.get(b"late") is None
    assert cache.stats()["entries"] == 0


def test_clear_and_mutation_isolation():
    cache = MemoryCache(enabled=True, ttl_ms=1000, max_entries=5, max_bytes=2000)
    value = {"metadata": ["original"]}
    cache.put(b"a", value)
    value["metadata"].append("caller mutation")
    hit = cache.get(b"a")
    hit["metadata"].append("consumer mutation")
    assert cache.get(b"a") == {"metadata": ["original"]}
    cache.clear()
    assert cache.get(b"a") is None


def test_disabled_does_not_keep_values():
    cache = MemoryCache(enabled=False, ttl_ms=1000, max_entries=5, max_bytes=2000)
    cache.put(b"a", [1.0, 2.0])
    assert cache.get(b"a") is None
    assert cache.stats()["entries"] == 0


def test_byte_budget_evicts_before_entry_budget():
    cache = MemoryCache(enabled=True, ttl_ms=1000, max_entries=100, max_bytes=1500)
    cache.put(b"a", "x" * 800)
    assert cache.get(b"a") is not None
    cache.put(b"b", "y" * 800)
    assert cache.get(b"a") is None
    assert cache.get(b"b") == "y" * 800
    assert cache.stats()["estimated_bytes"] <= 1500
    assert cache.stats()["evicted"] == 1
