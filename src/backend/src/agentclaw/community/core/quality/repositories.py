"""Quality Task Repository Protocol and Record.

Defines the abstract interface for quality task persistence operations.
Implementation provided in plugins/quality_repository.py (unified ORM).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class QualityTaskRecord:
    """Database record for ac_bot_quality_task."""

    id: int
    uuid: str | None
    task_type: str
    biz_type: str
    status: str
    bot_id: str | None
    owner_id: str | None
    ext: dict[str, Any]  # JSON field, parsed as dict
    operator_id: str | None
    env: str | None
    gmt_create: datetime | None
    gmt_modified: datetime | None


@runtime_checkable
class QualityTaskRepository(Protocol):
    """Protocol for quality task repository implementations.

    Implementation: a single unified ORM body at
    ``plugins.quality_repository.QualityTaskRepository`` (runs on both
    the corp store and SQLite via the injected ``DatabasePlugin``).
    """

    def list_by_conditions(
        self,
        *,
        task_type: str,
        biz_type: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[QualityTaskRecord], int]:
        """List quality tasks by conditions with pagination.

        Args:
            task_type: Task type filter (required, e.g., "eval")
            biz_type: Business type filter (required, e.g., "service_bot_single")
            bot_id: Optional bot ID filter
            owner_id: Optional owner ID filter
            page: Page number (1-indexed)
            page_size: Page size

        Returns:
            Tuple of (list of records, total count)
        """
        ...

    def get_by_uuid(self, uuid: str) -> QualityTaskRecord | None:
        """Get a quality task by UUID.

        Args:
            uuid: Task UUID

        Returns:
            QualityTaskRecord if found, None otherwise
        """
        ...

    def get_by_id(self, id: int) -> QualityTaskRecord | None:
        """Get a quality task by ID.

        Args:
            id: Task ID (primary key)

        Returns:
            QualityTaskRecord if found, None otherwise
        """
        ...

    def create(
        self,
        *,
        uuid: str | None = None,
        task_type: str,
        biz_type: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        ext: dict[str, Any] | None = None,
        operator_id: str | None = None,
    ) -> QualityTaskRecord:
        """Create a new quality task.

        Args:
            uuid: Optional task UUID
            task_type: Task type
            biz_type: Business type
            bot_id: Optional bot ID
            owner_id: Optional owner ID
            ext: Optional extension data (JSON)
            operator_id: Optional operator ID

        Returns:
            Created QualityTaskRecord
        """
        ...

    def update_status(
        self, id: int, status: str, ext: dict[str, Any] | None = None
    ) -> QualityTaskRecord | None:
        """Update the status of a quality task.

        Args:
            id: Task ID (primary key)
            status: New status
            ext: Optional extension data to merge/update

        Returns:
            Updated QualityTaskRecord if found, None otherwise
        """
        ...

    def update_ext(self, id: int, ext: dict[str, Any]) -> QualityTaskRecord | None:
        """Update only the ext field of a quality task.

        Args:
            id: Task ID (primary key)
            ext: Extension data to merge/update

        Returns:
            Updated QualityTaskRecord if found, None otherwise
        """
        ...
