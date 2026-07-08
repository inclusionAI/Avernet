"""``WorkItemService`` — vendor-neutral work-item CRUD contract.

Implementations:
- CORP: plugin backed by the internal work-item product (under ``plugins/prod/``)
- COMMUNITY/TEST: ``NoopWorkItemService`` (raises NotImplementedError → HTTP 501)

The Protocol takes primitives / neutral DTOs only — never vendor identifiers
surface here. ``staff_id`` is the neutral caller identity (used for auth by the
backing product), not a vendor concept.
"""
from __future__ import annotations

from typing import Protocol

from engine.community.plugin_api.work_item.models import WorkItem, WorkItemCreate, WorkItemRef


class WorkItemService(Protocol):
    async def list_work_items(self, space_ref: str, staff_id: str) -> list[WorkItem]:
        """List work items within the space referenced by ``space_ref``."""
        ...

    async def get_work_item(self, ref: WorkItemRef, staff_id: str) -> WorkItem:
        """Fetch a single work item identified by ``ref``."""
        ...

    async def create_work_item(self, req: WorkItemCreate) -> WorkItem:
        """Create a work item. Caller identity is ``req.staff_id``."""
        ...
