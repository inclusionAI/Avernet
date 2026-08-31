"""Service API Protocol for skill batch sync."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillBatchSyncServiceProtocol(Protocol):
    """Service API for batch-syncing skills across the fleet."""

    def run(self, *args: Any, **kwargs: Any) -> Any: ...
