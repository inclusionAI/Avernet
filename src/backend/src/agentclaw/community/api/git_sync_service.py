"""Service API Protocol for the skill_center git-sync service."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GitSyncServiceProtocol(Protocol):
    """Service API for git-based skill sync."""

    async def sync(self, *args: Any, **kwargs: Any) -> Any: ...

    async def sync_bootstrap(self, *args: Any, **kwargs: Any) -> Any: ...
