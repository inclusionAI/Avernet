"""API Key 验证模块

高频操作，只读，可扩展缓存/限流
"""

import hashlib
import threading
import time
from collections import OrderedDict
from typing import cast

from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.core.repository.api_gateway import APIKeyRepository
from secbaas.community.core.utils import env_utils
from secbaas.community.logger import get_logger

from ._key_gen import APIKeyGenerator
from ._protocols import APIKeyValidator

logger = get_logger("core-service")

_MISS = object()


class _TTLCache:
    """进程内有界 TTL 缓存（线程安全）。

    verify 的 PBKDF2（100k 迭代）+ 前缀 DB 查询是每请求热路径；
    缓存 key 使用 sha256(api_key)，内存中不保留明文 key。
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[object, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> object:
        """返回缓存值；未缓存或已过期返回 ``_MISS``。"""
        entry = self._entries.get(key)
        if entry is None:
            return _MISS
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            with self._lock:
                self._entries.pop(key, None)
            return _MISS
        return value

    def put(self, key: str, value: object, ttl: float) -> None:
        with self._lock:
            self._entries[key] = (value, time.monotonic() + ttl)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


class DefaultAPIKeyValidator(APIKeyValidator):
    """API Key 验证器默认实现"""

    #: 正结果缓存 TTL：停用/吊销的 key 最迟在 TTL 后失效。
    _POSITIVE_TTL_SECONDS = 60.0
    #: 负结果缓存 TTL：控制新建/激活 key 后的可见延迟上限。
    _NEGATIVE_TTL_SECONDS = 5.0

    def __init__(self, repository: APIKeyRepository):
        self._repository = repository
        self._cache = _TTLCache()

    async def verify(self, api_key: str) -> APIKeyRecord | None:
        """验证 API Key 有效性（命中进程内 TTL 缓存）

        Args:
            api_key: 完整的 API Key

        Returns:
            验证通过返回 APIKeyRecord，失败返回 None
        """
        return self._verify(api_key)

    def verify_sync(self, api_key: str) -> APIKeyRecord | None:
        """同步验证 API Key 有效性（用于非异步场景，与 verify 共享缓存）

        Args:
            api_key: 完整的 API Key

        Returns:
            验证通过返回 APIKeyRecord，失败返回 None
        """
        return self._verify(api_key)

    def _verify(self, api_key: str) -> APIKeyRecord | None:
        if not api_key or len(api_key) < 8:
            logger.warning("[verify] Invalid api_key format")
            return None

        # 缓存 key 带 env 前缀：与查询口径一致，避免跨环境串缓存。
        env = env_utils.get_current_env()
        cache_key = f"{env}:{hashlib.sha256(api_key.encode()).hexdigest()}"
        cached = self._cache.get(cache_key)
        if cached is not _MISS:
            return cast("APIKeyRecord | None", cached)

        prefix = api_key[:8]

        # 鉴权钉死当前环境：共享 DB 下避免跨环境 API Key 互认。
        # env 口径与创建侧一致（均经 env_utils.get_current_env 归一）。
        record = self._repository.get_by_prefix_and_status(prefix, "ACTIVE", env=env)

        if record is None:
            logger.debug(f"[verify] No active key found for prefix: {prefix}")
            self._cache.put(cache_key, None, self._NEGATIVE_TTL_SECONDS)
            return None

        if not APIKeyGenerator.verify_key(api_key, record.api_key_hash):
            logger.warning(f"[verify] Hash verification failed for prefix: {prefix}")
            self._cache.put(cache_key, None, self._NEGATIVE_TTL_SECONDS)
            return None

        logger.debug(
            f"[verify] API Key verified: id={record.id}, app_id={record.app_id}"
        )
        self._cache.put(cache_key, record, self._POSITIVE_TTL_SECONDS)
        return record
