"""
Skill Cache - 市场缓存与全局同步锁

从 skill_service.py 中提取的独立模块：
- MarketCache: 全局市场技能缓存（分布式缓存 + 内存降级）
- GlobalSyncLock: 全局同步锁（内存级，用于频率控制）
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from injector import inject

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin


logger = get_logger()


# ============================================================================
# Market Cache - 全局市场缓存（支持 分布式缓存 + 内存缓存降级）
# ============================================================================

class MarketCache:
    """全局市场技能缓存

    优先使用 分布式缓存，失败时降级到内存缓存。
    缓存键使用环境变量区分，全局共享。
    """

    # 缓存键前缀
    CACHE_KEY_PREFIX = "market"

    # 内存缓存 TTL（仅降级时使用，实际上由定时任务控制更新，这里设置较长）
    MEMORY_CACHE_TTL = 3600  # 1小时

    # 分布式缓存 可用性探测结果的复检间隔（秒）。
    # 探测结果带 TTL 复检，避免一旦探测为不可用/可用就永久缓存：
    # 分布式缓存 中途故障→恢复时，最多 ZCACHE_RECHECK_TTL 秒后自动切回，无需重启进程。
    ZCACHE_RECHECK_TTL = 60

    @inject
    def __init__(self, cache_plugin: CachePlugin):
        self._memory_cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._zcache_available: bool | None = None  # None=未检测, True=可用, False=不可用
        self._zcache_checked_at: float = 0.0  # 上次探测时间戳（用于 TTL 复检）
        self._cache_plugin = cache_plugin

    def _get_env(self) -> str:
        """获取当前环境"""
        try:
            from agentclaw.community.utils.env_utils import get_current_env
            return get_current_env() or "dev"
        except Exception:
            return "dev"

    def _build_key(self, base_key: str) -> str:
        """构建全局缓存键"""
        env = self._get_env()
        return f"{self.CACHE_KEY_PREFIX}:{base_key}:{env}"

    def _check_zcache_available(self) -> bool:
        """检查 分布式缓存 是否可用（带 TTL 复检）。

        探测结果缓存 ``ZCACHE_RECHECK_TTL`` 秒。超过 TTL 后重新探测一次，
        使得 分布式缓存 故障→恢复（或反之）能在一个复检周期内被感知，无需重启进程。
        历史 bug：探测结果被永久缓存，分布式缓存 中途恢复后仍走内存降级，必须重启。
        """
        now = time.time()
        if (
            self._zcache_available is not None
            and (now - self._zcache_checked_at) < self.ZCACHE_RECHECK_TTL
        ):
            return self._zcache_available

        try:
            cache = self._cache_plugin
            # 尝试一个简单的操作来验证 分布式缓存 是否可用
            test_key = f"{self.CACHE_KEY_PREFIX}:test:connection"
            cache.get(test_key)
            if self._zcache_available is not True:
                logger.info("[MarketCache] distributed cache is available")
            self._zcache_available = True
        except Exception as e:
            if self._zcache_available is not False:
                logger.warning(f"[MarketCache] distributed cache unavailable, fallback to memory cache: {e}")
            self._zcache_available = False
        self._zcache_checked_at = now
        return self._zcache_available

    def get(self, base_key: str) -> Any | None:
        """获取缓存数据

        优先从 分布式缓存 获取，失败时从内存缓存获取。
        """
        full_key = self._build_key(base_key)

        # 尝试 分布式缓存
        if self._check_zcache_available():
            try:
                cache = self._cache_plugin
                data = cache.get_json(full_key)
                if data is not None:
                    logger.info(f"[MarketCache] HIT: key={full_key}, source=zcache")
                    return data
            except Exception as e:
                logger.warning(f"[MarketCache] distributed cache get failed: {e}")

        # 降级到内存缓存
        with self._lock:
            if full_key in self._memory_cache:
                cache_entry = self._memory_cache[full_key]
                elapsed = time.time() - cache_entry.get('timestamp', 0)
                if elapsed < self.MEMORY_CACHE_TTL:
                    logger.info(f"[MarketCache] HIT: key={full_key}, source=memory")
                    return cache_entry.get('data')

        logger.info(f"[MarketCache] MISS: key={full_key}")
        return None

    def set(self, base_key: str, data: Any) -> bool:
        """存储缓存数据

        优先存储到 分布式缓存，失败时存储到内存缓存。
        """
        full_key = self._build_key(base_key)
        data_size = len(json.dumps(data)) if data else 0

        # 尝试 分布式缓存
        if self._check_zcache_available():
            try:
                cache = self._cache_plugin
                if cache.set_json(full_key, data):
                    logger.info(f"[MarketCache] SET: key={full_key}, source=zcache, size={data_size} bytes")
                    return True
            except Exception as e:
                logger.warning(f"[MarketCache] distributed cache set failed: {e}")

        # 降级到内存缓存
        with self._lock:
            self._memory_cache[full_key] = {
                'data': data,
                'timestamp': time.time()
            }
        logger.info(f"[MarketCache] SET: key={full_key}, source=memory, size={data_size} bytes")
        return True

    def invalidate(self) -> None:
        """清除所有市场缓存"""
        env = self._get_env()

        # 清除 分布式缓存
        if self._check_zcache_available():
            try:
                cache = self._cache_plugin
                for key_suffix in ['market_tree', 'market_skills_list', 'market_skills_flat']:
                    full_key = self._build_key(key_suffix)
                    cache.delete(full_key)
                logger.info(f"[MarketCache] distributed cache invalidated for env={env}")
            except Exception as e:
                logger.warning(f"[MarketCache] distributed cache invalidate failed: {e}")

        # 清除内存缓存（只清除当前环境的 key）
        with self._lock:
            keys_to_clear = [k for k in self._memory_cache if f":{env}" in k]
            for key in keys_to_clear:
                del self._memory_cache[key]
        logger.info(f"[MarketCache] Memory cache invalidated for env={env}, cleared {len(keys_to_clear)} keys")


# ============================================================================
# Global Sync Lock - 全局同步锁
# ============================================================================

# 全局同步状态记录（用于只读文件系统下的频率控制）
_global_sync_state = {
    "last_sync_time": 0,
    "is_syncing": False,
    "sync_lock_time": 0
}


class GlobalSyncLock:
    """全局同步锁（内存级，适用于只读文件系统）"""
    _lock = threading.Lock()

    @classmethod
    def acquire(cls, timeout: int = 60) -> bool:
        """尝试获取锁，返回是否成功"""
        global _global_sync_state
        with cls._lock:
            now = time.time()
            if _global_sync_state["is_syncing"]:
                # 检查锁是否超时
                if now - _global_sync_state["sync_lock_time"] < timeout:
                    return False
            # 获取锁成功
            _global_sync_state["is_syncing"] = True
            _global_sync_state["sync_lock_time"] = now
            return True

    @classmethod
    def release(cls):
        """释放锁"""
        global _global_sync_state
        with cls._lock:
            _global_sync_state["is_syncing"] = False
            _global_sync_state["sync_lock_time"] = 0

    @classmethod
    def update_sync_time(cls):
        """更新最后同步时间"""
        global _global_sync_state
        with cls._lock:
            _global_sync_state["last_sync_time"] = time.time()

    @classmethod
    def check_rate_limit(cls, min_interval: int) -> tuple[bool, int]:
        """检查频率限制，返回 (是否允许同步, 距离下次可同步的秒数)"""
        global _global_sync_state
        with cls._lock:
            now = time.time()
            elapsed = now - _global_sync_state["last_sync_time"]
            if elapsed < min_interval:
                return False, int(min_interval - elapsed)
            return True, 0

    @classmethod
    def record_sync(cls):
        """记录同步完成时间"""
        global _global_sync_state
        with cls._lock:
            _global_sync_state["last_sync_time"] = time.time()
