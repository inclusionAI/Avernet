"""Service API Protocol for task processor."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.quality.models import QualityTaskRecord


@runtime_checkable
class TaskProcessorProtocol(Protocol):
    """Service API for advancing task status."""

    async def process(self, id: int) -> QualityTaskRecord:
        """Advance task to the next status in the workflow.

        Status flow: init → env_preparing → env_ready → running → success/failed

        If the task is already at a terminal status (success/failed), returns the
        task unchanged instead of raising an error.

        Args:
            id: Task ID (primary key)

        Returns:
            Updated QualityTaskRecord (or current record if at terminal status)

        Raises:
            ValueError: If task not found
        """
        ...

    async def to_env_preparing(self, id: int) -> QualityTaskRecord:
        """Start environment preparation, advance task from 'init' to 'env_preparing' status."""
        ...

    def to_env_ready(self, id: int) -> QualityTaskRecord:
        """Mark environment ready, advance task from 'env_preparing' to 'env_ready' status."""
        ...

    def to_running(self, id: int) -> QualityTaskRecord:
        """Advance task from 'env_ready' to 'running' status."""
        ...

    def to_success(self, id: int) -> QualityTaskRecord:
        """Advance task from 'running' to 'success' status."""
        ...

    def to_failed(self, id: int) -> QualityTaskRecord:
        """Advance task from 'running' to 'failed' status."""
        ...
