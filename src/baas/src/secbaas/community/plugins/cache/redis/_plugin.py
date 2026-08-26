"""Redis cache plugin — redis-backed implementation of the baas CachePlugin SPI.

Implements :class:`~secbaas.community.spi.cache.CachePlugin` using Redis as the
backing store, with TTL-based expiration handled server-side by Redis
(``SET key value EX ttl_seconds``). Selected via ``plugins.cache = redis``,
complementing the existing stub.

Connection settings come from :class:`RedisCacheConfig`; concrete credentials
(password) are resolved by the composition root before the client is built, so
the plugin never sees secret references and never starts an empty-value
connection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import redis as redis_lib

from secbaas.community.spi.cache import CachePlugin

if TYPE_CHECKING:
    from ._config import RedisCacheConfig


class RedisCachePlugin(CachePlugin):
    """Redis-backed key-value cache plugin.

    Args:
        config: Redis connection configuration with already-resolved values.
        client: Optional pre-built Redis client. When omitted, one is built
            lazily from ``config`` on first use. Injectable for tests.
    """

    def __init__(
        self,
        config: RedisCacheConfig | dict[str, Any],
        client: redis_lib.Redis | None = None,
    ) -> None:
        if isinstance(config, dict):
            from ._config import RedisCacheConfig

            config = RedisCacheConfig(**config)
        self._config = config
        self._client = client

    def _get_client(self) -> redis_lib.Redis:
        if self._client is None:
            self._client = redis_lib.Redis(
                host=self._config.host,
                port=self._config.port,
                username=self._config.username or None,
                password=self._config.password or None,
                db=self._config.db,
                ssl=self._config.ssl,
                socket_timeout=self._config.socket_timeout,
            )
        return self._client

    def get(self, key: str) -> str | None:
        client = self._get_client()
        value: object = client.get(key)
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return cast(str, value)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        client = self._get_client()
        client.set(key, value, ex=ttl_seconds)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
