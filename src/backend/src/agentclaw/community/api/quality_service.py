"""Service API Protocol for quality task management."""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from agentclaw.community.core.quality.repositories import QualityTaskRecord


@runtime_checkable
class QualityTaskServiceProtocol(Protocol):
    """Service API for quality task management."""

    def list_tasks(
        self,
        *,
        task_type: str,
        biz_type: str,
        bot_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[QualityTaskRecord], int]:
        """List quality tasks by conditions with pagination."""
        ...

    def get_task_by_uuid(self, uuid: str) -> Optional[QualityTaskRecord]:
        """Get a quality task by UUID."""
        ...

    def get_task_by_id(self, id: int) -> Optional[QualityTaskRecord]:
        """Get a quality task by ID."""
        ...

    def create_task(
        self,
        *,
        task_type: str,
        biz_type: str,
        bot_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        ext: Optional[dict[str, Any]] = None,
        operator_id: Optional[str] = None,
    ) -> QualityTaskRecord:
        """Create a new quality task.

        UUID is generated internally by the service.
        """
        ...

    def update_task_status(
        self, id: int, status: str, ext: Optional[dict[str, Any]] = None
    ) -> Optional[QualityTaskRecord]:
        """Update the status of a quality task by ID.

        Args:
            id: Task ID (primary key)
            status: New status
            ext: Optional extension data to merge/update

        Returns:
            Updated QualityTaskRecord if found, None otherwise
        """
        ...
