"""Tests for LocalMemoryCacheManager."""
import time
import threading


from agentclaw.community.plugins.local.cache import MemoryCachePlugin


class TestLocalMemoryCacheBasicOps:
    """Basic get/set/delete operations."""

    def test_set_and_get(self):
        cache = MemoryCachePlugin()
        assert cache.set("key1", "value1") is True
        assert cache.get("key1") == "value1"

    def test_get_nonexistent_key(self):
        cache = MemoryCachePlugin()
        assert cache.get("missing") is None

    def test_delete(self):
        cache = MemoryCachePlugin()
        cache.set("key1", "value1")
        assert cache.delete("key1") is True
        assert cache.get("key1") is None

    def test_delete_nonexistent_key(self):
        cache = MemoryCachePlugin()
        assert cache.delete("missing") is True

    def test_overwrite(self):
        cache = MemoryCachePlugin()
        cache.set("key1", "v1")
        cache.set("key1", "v2")
        assert cache.get("key1") == "v2"


class TestLocalMemoryCacheTTL:
    """TTL (expiry) behavior."""

    def test_ttl_not_expired(self):
        cache = MemoryCachePlugin()
        cache.set("key1", "value1", ttl=10)
        assert cache.get("key1") == "value1"

    def test_ttl_expired(self):
        cache = MemoryCachePlugin()
        cache.set("key1", "value1", ttl=1)
        time.sleep(1.1)
        assert cache.get("key1") is None

    def test_no_ttl_never_expires(self):
        cache = MemoryCachePlugin()
        cache.set("key1", "value1", ttl=0)
        assert cache.get("key1") == "value1"


class TestLocalMemoryCacheJSON:
    """JSON get/set operations."""

    def test_set_and_get_json(self):
        cache = MemoryCachePlugin()
        data = {"name": "test", "count": 42}
        assert cache.set_json("json_key", data) is True
        result = cache.get_json("json_key")
        assert result == data

    def test_get_json_nonexistent(self):
        cache = MemoryCachePlugin()
        assert cache.get_json("missing") is None

    def test_get_json_invalid_json(self):
        cache = MemoryCachePlugin()
        cache.set("bad_json", "not-a-json-string")
        assert cache.get_json("bad_json") is None


class TestLocalMemoryCacheLock:
    """Distributed lock operations (process-local in local mode)."""

    def test_acquire_lock_success(self):
        cache = MemoryCachePlugin()
        lock_value = cache.acquire_lock("my_lock", ttl=30)
        assert lock_value is not None
        assert isinstance(lock_value, str)

    def test_acquire_lock_already_held(self):
        cache = MemoryCachePlugin()
        first = cache.acquire_lock("my_lock", ttl=30)
        assert first is not None
        second = cache.acquire_lock("my_lock", ttl=30)
        assert second is None

    def test_release_lock_success(self):
        cache = MemoryCachePlugin()
        lock_value = cache.acquire_lock("my_lock", ttl=30)
        assert cache.release_lock("my_lock", lock_value) is True
        # Can acquire again after release
        new_lock = cache.acquire_lock("my_lock", ttl=30)
        assert new_lock is not None

    def test_release_lock_wrong_value(self):
        cache = MemoryCachePlugin()
        cache.acquire_lock("my_lock", ttl=30)
        assert cache.release_lock("my_lock", "wrong_value") is False

    def test_release_lock_not_held(self):
        cache = MemoryCachePlugin()
        assert cache.release_lock("no_lock", "any_value") is False

    def test_lock_expires(self):
        cache = MemoryCachePlugin()
        cache.acquire_lock("my_lock", ttl=1)
        time.sleep(1.1)
        # Lock expired, can acquire again
        new_lock = cache.acquire_lock("my_lock", ttl=30)
        assert new_lock is not None

    def test_lock_full_pattern(self):
        """Test the typical lock usage pattern."""
        cache = MemoryCachePlugin()
        lock_value = cache.acquire_lock("task_lock", ttl=60)
        assert lock_value is not None
        try:
            # Simulate work
            cache.set("result", "done")
        finally:
            released = cache.release_lock("task_lock", lock_value)
            assert released is True


class TestLocalMemoryCacheThreadSafety:
    """Thread safety tests."""

    def test_concurrent_set_get(self):
        cache = MemoryCachePlugin()
        errors = []

        def writer(thread_id):
            try:
                for i in range(100):
                    cache.set(f"t{thread_id}_k{i}", f"v{i}")
            except Exception as e:
                errors.append(e)

        def reader(thread_id):
            try:
                for i in range(100):
                    cache.get(f"t{thread_id}_k{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for t_id in range(5):
            threads.append(threading.Thread(target=writer, args=(t_id,)))
            threads.append(threading.Thread(target=reader, args=(t_id,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
