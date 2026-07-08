"""Quality Task Service.

Business logic for quality task management.
"""
import uuid

from injector import inject

from typing import Any

from agentclaw.community.core.quality.repositories import (
    QualityTaskRepository,
    QualityTaskRecord,
)
from agentclaw.community.log import get_logger


logger = get_logger()


class QualityTaskService:
    """Quality task business logic service."""

    @inject
    def __init__(self, repository: QualityTaskRepository) -> None:
        self._repository = repository

    def list_tasks(
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
            task_type: Task type filter (required)
            biz_type: Business type filter (required)
            bot_id: Optional bot ID filter
            owner_id: Optional owner ID filter
            page: Page number (1-indexed)
            page_size: Page size

        Returns:
            Tuple of (list of records, total count)
        """
        logger.info("[list_tasks] task_type=%s, biz_type=%s, bot_id=%s, owner_id=%s, page=%s, page_size=%s", task_type, biz_type, bot_id, owner_id, page, page_size)
        return self._repository.list_by_conditions(
            task_type=task_type,
            biz_type=biz_type,
            bot_id=bot_id,
            owner_id=owner_id,
            page=page,
            page_size=page_size,
        )

    def get_task_by_uuid(self, uuid: str) -> QualityTaskRecord | None:
        """Get a quality task by UUID.

        Args:
            uuid: Task UUID

        Returns:
            QualityTaskRecord if found, None otherwise
        """
        logger.info("[get_task_by_uuid] uuid=%s", uuid)
        return self._repository.get_by_uuid(uuid)

    def get_task_by_id(self, id: int) -> QualityTaskRecord | None:
        """Get a quality task by ID.

        Args:
            id: Task ID (primary key)

        Returns:
            QualityTaskRecord if found, None otherwise
        """
        logger.info("[get_task_by_id] id=%s", id)
        return self._repository.get_by_id(id)

    def create_task(
        self,
        *,
        task_type: str,
        biz_type: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
        ext: dict | None = None,
        operator_id: str | None = None,
    ) -> QualityTaskRecord:
        """Create a new quality task.

        Args:
            task_type: Task type
            biz_type: Business type
            bot_id: Optional bot ID
            owner_id: Optional owner ID
            ext: Optional extension data (JSON)
            operator_id: Optional operator ID

        Returns:
            Created QualityTaskRecord
        """
        task_uuid = uuid.uuid4().hex
        logger.info("[create_task] uuid=%s, task_type=%s, biz_type=%s, bot_id=%s, owner_id=%s", task_uuid, task_type, biz_type, bot_id, owner_id)
        return self._repository.create(
            uuid=task_uuid,
            task_type=task_type,
            biz_type=biz_type,
            bot_id=bot_id,
            owner_id=owner_id,
            ext=ext,
            operator_id=operator_id,
        )

    def update_task_status(
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
        logger.info("[update_task_status] id=%s, status=%s, ext=%s", id, status, ext)
        return self._repository.update_status(id, status, ext)
