"""Cache plugin Protocol — KV operations + distributed locking."""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


class CacheLockInfrastructureError(RuntimeError):
    """The cache backend could not answer a distributed-lock operation.

    This is deliberately distinct from a healthy ``SET NX`` miss: callers
    which protect data-plane mutations need to expose an infrastructure
    failure instead of telling users that another request owns the lock.
    """


@runtime_checkable
class CachePlugin(Plugin, Protocol):
    """Cache manager interface supporting KV operations and distributed locking."""

    def get(self, key: str) -> Optional[str]:
        ...

    def set(self, key: str, value: str, ttl: int = 0) -> bool:
        ...

    def delete(self, key: str) -> bool:
        ...

    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        ...

    def set_json(self, key: str, value: Dict[str, Any], ttl: int = 0) -> bool:
        ...

    def acquire_lock(self, lock_key: str, ttl: int = 30) -> Optional[str]:
        ...

    def acquire_lock_strict(self, lock_key: str, ttl: int = 30) -> Optional[str]:
        """Acquire a lock, raising ``CacheLockInfrastructureError`` on outage.

        ``None`` means only that a healthy cache reports the lock as busy.
        The legacy ``acquire_lock`` keeps its best-effort, ``None``-on-outage
        contract for existing callers.
        """
        ...

    def release_lock(self, lock_key: str, lock_value: str) -> bool:
        ...
