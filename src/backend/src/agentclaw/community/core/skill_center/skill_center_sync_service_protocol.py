"""Service API Protocol for skill_center sync."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillCenterSyncServiceProtocol(Protocol):
    """Service API for syncing skills with the SkillCenter remote."""

    def force_sync(self, *args: Any, **kwargs: Any) -> Any: ...

    def is_synced(self, *args: Any, **kwargs: Any) -> Any: ...

    def cleanup_stale(self, *args: Any, **kwargs: Any) -> Any: ...

    async def sync_bootstrap(self, *args: Any, **kwargs: Any) -> Any: ...
