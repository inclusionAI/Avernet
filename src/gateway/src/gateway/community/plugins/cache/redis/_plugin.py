"""Redis cache plugin — Redis-backed key-value cache with TTL.

Uses a synchronous redis-py client. The connection is established in
``__init__`` so that connection errors surface at startup rather than
on the first ``get`` / ``set`` call. Selected via ``plugins.cache = redis``.
"""

from __future__ import annotations

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from gateway.community.logger import get_logger
from gateway.community.spi.cache import CachePlugin

logger = get_logger("cache")


class RedisCachePlugin(CachePlugin):
    """Redis-backed cache plugin.

    Args:
        url: Redis connection URL (e.g. ``redis://localhost:6379/0``).
        socket_timeout: Per-command socket timeout in seconds.
        socket_connect_timeout: Initial connection timeout in seconds.
    """

    def __init__(
        self,
        url: str,
        *,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
    ) -> None:
        self._redis: Redis = Redis.from_url(
            url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            decode_responses=True,
        )
        try:
            self._redis.ping()
        except (RedisConnectionError, RedisTimeoutError) as e:
            raise RuntimeError(f"Cannot connect to Redis at {url}: {e}") from e
        logger.info("RedisCachePlugin connected to %s", url)

    def get(self, key: str) -> str | None:
        return self._redis.get(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._redis.set(key, value, ex=ttl_seconds)

    def close(self) -> None:
        self._redis.close()
        logger.info("RedisCachePlugin connection closed")
