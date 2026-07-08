"""CommunityCache — community CachePlugin (KV + distributed lock).

Covers both method families on one backend, matching corp (where ZCache serves
both): key/value cache and a distributed lock. The backend is chosen at
construction from the configured ``redis_url``:

- **Redis** (``redis_url`` set): the exact algorithm corp runs — ``SET key token
  NX EX ttl`` to acquire, an atomic compare-and-delete Lua script to release —
  against a plain ``redis.Redis``. Correct for a multi-worker deploy.
- **In-process** (``redis_url`` empty): a dict + ``threading.Lock`` + TTL, with
  a process-local lock. **Single-process only** — the in-process lock is NOT
  shared across workers, so a horizontally-scaled deploy without Redis would get
  an unsafe lock. Intended for single-node development.

A real, deployable implementation (not a ``MockSeam`` test double).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Dict, Optional

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.cache import CachePlugin


logger = get_logger()


def _new_lock_token() -> str:
    return f"{uuid.uuid4().hex[:8]}:{int(time.time())}"


class _RedisBackend:
    """KV + distributed lock over a plain ``redis.Redis`` (corp's algorithm)."""

    # Atomic compare-and-delete: release the lock only if we still hold it.
    _RELEASE_LOCK_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, redis_url: str) -> None:
        import redis  # community dep; imported lazily so non-cache boots skip it

        # ``from_url`` does not connect — the first command does.
        self._redis = redis.Redis.from_url(redis_url)

    def get(self, key: str) -> Optional[str]:
        try:
            result = self._redis.get(key)
            return result.decode("utf-8") if result is not None else None
        except Exception as e:
            logger.error(
                "[CACHE-INFRA-FAILURE] Cache get error: key=%s, error=%s",
                key, e, exc_info=True,
            )
            return None

    def set(self, key: str, value: str, ttl: int = 0) -> bool:
        try:
            if ttl > 0:
                self._redis.set(key, value, ex=ttl)
            else:
                self._redis.set(key, value)
            return True
        except Exception as e:
            logger.error(
                "[CACHE-INFRA-FAILURE] Cache set error: key=%s, error=%s",
                key, e, exc_info=True,
            )
            return False

    def delete(self, key: str) -> bool:
        try:
            self._redis.delete(key)
            return True
        except Exception as e:
            logger.error(
                "[CACHE-INFRA-FAILURE] Cache delete error: key=%s, error=%s",
                key, e, exc_info=True,
            )
            return False

    def acquire_lock(self, lock_key: str, ttl: int = 30) -> Optional[str]:
        try:
            token = _new_lock_token()
            ok = self._redis.set(f"lock:{lock_key}", token, nx=True, ex=ttl)
            if ok:
                return token
            # Healthy "lock busy": the key already exists, SET NX returned falsy.
            logger.debug(
                "Cache acquire_lock: lock held by others, key=%s", lock_key
            )
            return None
        except Exception as e:
            # Infrastructure failure (Redis unreachable) — NOT lock contention.
            # Logged distinctly so it is not misread as "lock held".
            logger.error(
                "[CACHE-INFRA-FAILURE] acquire_lock could not reach cache "
                "backend (key=%s): %s — NOT a lock contention",
                lock_key, e, exc_info=True,
            )
            return None

    def release_lock(self, lock_key: str, lock_value: str) -> bool:
        storage_key = f"lock:{lock_key}"
        try:
            try:
                result = self._redis.eval(
                    self._RELEASE_LOCK_SCRIPT, 1, storage_key, lock_value
                )
                if result == 0:
                    logger.warning(
                        "Cache release_lock: value mismatch (atomic), key=%s",
                        lock_key,
                    )
                    return False
                return True
            except Exception as lua_err:
                # eval unavailable → non-atomic GET+DELETE fallback.
                logger.debug(
                    "Cache release_lock: Lua eval not available, fallback: %s",
                    lua_err,
                )
                current = self._redis.get(storage_key)
                if current is not None:
                    current = current.decode("utf-8")
                if current != lock_value:
                    logger.warning(
                        "Cache release_lock: value mismatch, key=%s", lock_key
                    )
                    return False
                self._redis.delete(storage_key)
                return True
        except Exception as e:
            logger.error(
                "[CACHE-INFRA-FAILURE] Cache release_lock error: key=%s, "
                "error=%s", lock_key, e, exc_info=True,
            )
            return False


class _InProcessBackend:
    """dict + ``threading.Lock`` + TTL; process-local lock. Single-process only."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}
        self._guard = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._guard:
            if key in self._store:
                value, expiry = self._store[key]
                if expiry is None or time.time() < expiry:
                    return value
                del self._store[key]
        return None

    def set(self, key: str, value: str, ttl: int = 0) -> bool:
        with self._guard:
            expiry = time.time() + ttl if ttl > 0 else None
            self._store[key] = (value, expiry)
        return True

    def delete(self, key: str) -> bool:
        with self._guard:
            self._store.pop(key, None)
        return True

    def acquire_lock(self, lock_key: str, ttl: int = 30) -> Optional[str]:
        token = _new_lock_token()
        storage_key = f"lock:{lock_key}"
        with self._guard:
            if storage_key in self._store:
                _, expiry = self._store[storage_key]
                if expiry is not None and time.time() >= expiry:
                    del self._store[storage_key]
                else:
                    return None
            self._store[storage_key] = (token, time.time() + ttl)
        return token

    def release_lock(self, lock_key: str, lock_value: str) -> bool:
        storage_key = f"lock:{lock_key}"
        with self._guard:
            if storage_key in self._store:
                current, _ = self._store[storage_key]
                if current == lock_value:
                    del self._store[storage_key]
                    return True
            return False


class CommunityCache(CachePlugin):
    """KV cache + distributed lock; Redis-backed when configured, else in-proc."""

    def __init__(self, redis_url: str) -> None:
        if redis_url:
            self._backend: _RedisBackend | _InProcessBackend = _RedisBackend(
                redis_url
            )
        else:
            logger.info(
                "CommunityCache: no redis_url — using the in-process backend "
                "(single-process only; unsafe for a multi-worker deploy)."
            )
            self._backend = _InProcessBackend()

    def get(self, key: str) -> Optional[str]:
        return self._backend.get(key)

    def set(self, key: str, value: str, ttl: int = 0) -> bool:
        return self._backend.set(key, value, ttl)

    def delete(self, key: str) -> bool:
        return self._backend.delete(key)

    def acquire_lock(self, lock_key: str, ttl: int = 30) -> Optional[str]:
        return self._backend.acquire_lock(lock_key, ttl)

    def release_lock(self, lock_key: str, lock_value: str) -> bool:
        return self._backend.release_lock(lock_key, lock_value)

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        value = self.get(key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

    def set_json(self, key: str, value: Dict[str, Any], ttl: int = 0) -> bool:
        try:
            return self.set(key, json.dumps(value), ttl)
        except Exception as e:
            logger.error("Cache set_json error: key=%s, error=%s", key, e)
            return False
