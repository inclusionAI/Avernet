"""Community work-item service: unsupported, raises NotImplementedError.

Rule 20/21: every Protocol has a community/local implementation. The neutral
router translates ``NotImplementedError`` to HTTP 501 so community callers get a
clear "not supported" instead of a silent empty result.
"""
from __future__ import annotations

from engine.community.plugin_api.work_item.models import WorkItem, WorkItemCreate, WorkItemRef

_NOT_SUPPORTED = "work-item service is not supported on this engine profile"


class NoopWorkItemService:
    async def list_work_items(self, space_ref: str, staff_id: str) -> list[WorkItem]:
        raise NotImplementedError(_NOT_SUPPORTED)

    async def get_work_item(self, ref: WorkItemRef, staff_id: str) -> WorkItem:
        raise NotImplementedError(_NOT_SUPPORTED)

    async def create_work_item(self, req: WorkItemCreate) -> WorkItem:
        raise NotImplementedError(_NOT_SUPPORTED)
