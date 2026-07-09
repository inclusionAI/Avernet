"""MemoryCachePlugin — local-mode CachePlugin backed by in-process dict with TTL."""

import json
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin
import threading
import uuid
import time
from typing import Any, Dict, Optional
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="in-memory dict + threading.Lock",
)
class MemoryCachePlugin(MockSeam, CachePlugin):
    """Local in-memory cache with TTL and process-local distributed lock simulation."""

    def __init__(self):
        self._store: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def _cleanup_expired(self):
        """清理过期的缓存项（在锁内调用）。"""
        now = time.time()
        expired = [k for k, (_, exp) in self._store.items() if exp is not None and now >= exp]
        for k in expired:
            del self._store[k]

    def get(self, key: str) -> Optional[str]:
        """获取缓存值。"""
        with self._lock:
            if key in self._store:
                value, expiry = self._store[key]
                if expiry is None or time.time() < expiry:
                    return value
                del self._store[key]
        return None

    def set(self, key: str, value: str, ttl: int = 0) -> bool:
        """设置缓存值。"""
        with self._lock:
            expiry = time.time() + ttl if ttl > 0 else None
            self._store[key] = (value, expiry)
        return True

    def delete(self, key: str) -> bool:
        """删除缓存。"""
        with self._lock:
            self._store.pop(key, None)
        return True

    def acquire_lock(self, lock_key: str, ttl: int = 30) -> Optional[str]:
        """获取分布式锁（本地模式下为进程内锁）。"""
        lock_value = f"{uuid.uuid4().hex[:8]}:{int(time.time())}"
        lock_storage_key = f"lock:{lock_key}"
        with self._lock:
            if lock_storage_key in self._store:
                _, expiry = self._store[lock_storage_key]
                if expiry is not None and time.time() >= expiry:
                    del self._store[lock_storage_key]
                else:
                    return None
            self._store[lock_storage_key] = (lock_value, time.time() + ttl)
        return lock_value

    def release_lock(self, lock_key: str, lock_value: str) -> bool:
        """释放分布式锁。"""
        lock_storage_key = f"lock:{lock_key}"
        with self._lock:
            if lock_storage_key in self._store:
                current_value, _ = self._store[lock_storage_key]
                if current_value == lock_value:
                    del self._store[lock_storage_key]
                    return True
            return False

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        """获取 JSON 缓存值。"""
        value = self.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

    def set_json(self, key: str, value: Dict[str, Any], ttl: int = 0) -> bool:
        """设置 JSON 缓存值。"""
        try:
            return self.set(key, json.dumps(value), ttl)
        except Exception as e:
            logger.error("Cache set_json error: key=%s, error=%s", key, e)
            return False
