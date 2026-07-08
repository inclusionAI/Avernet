"""Cache plugin Protocol — KV operations + distributed locking."""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


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

    def release_lock(self, lock_key: str, lock_value: str) -> bool:
        ...
