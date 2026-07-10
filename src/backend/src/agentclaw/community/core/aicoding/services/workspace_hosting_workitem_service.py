"""Work-item service — delegates to WorkspaceHostingClient."""
from __future__ import annotations

import logging
from typing import Any, Dict

from injector import inject

from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client import (
    WorkspaceHostingClient,
)

logger = logging.getLogger(__name__)


class WorkspaceHostingWorkItemService:
    """Service for hosted-workspace work-item operations."""

    @inject
    def __init__(self, client: WorkspaceHostingClient) -> None:
        self._client = client

    def create_work_item(
        self,
        staff_id: str,
        request_body: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._client.create_work_item(
            staff_id=staff_id,
            request_body=request_body,
        )

    def create_work_item_relation(
        self,
        operator: str,
        request_body: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._client.create_work_item_relation(
            operator=operator,
            request_body=request_body,
        )

    def upload_file_to_arkgw(
        self,
        staff_id: str,
        source_id: str,
        file_content: bytes | None = None,
        file_name: str | None = None,
        content_type: str = "application/octet-stream",
        url: str | None = None,
    ) -> Dict[str, Any]:
        return self._client.upload_file_to_arkgw(
            staff_id=staff_id,
            source_id=source_id,
            file_content=file_content,
            file_name=file_name,
            content_type=content_type,
            url=url,
        )

    def update_work_item_document(
        self,
        staff_id: str,
        work_item_id: str,
        content: str,
        format_type: str = "MARKDOWN",
        editor_type: str = "YUQUE",
    ) -> Dict[str, Any]:
        return self._client.update_work_item_document(
            staff_id=staff_id,
            work_item_id=work_item_id,
            content=content,
            format_type=format_type,
            editor_type=editor_type,
        )
