"""Vendor-neutral work-item HTTP router.

Exposes ``/api/work-items/*`` CRUD. The router translates HTTP <-> neutral
``WorkItem`` DTOs only; it never references any vendor. The concrete
``WorkItemService`` is provided through FastAPI dependency injection by the
application composition root (corp -> internal product; community/test -> Noop,
which yields HTTP 501).
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from engine.community.di import Injected
from engine.community.plugin_api.work_item.models import WorkItemCreate, WorkItemRef
from engine.community.plugin_api.work_item.protocol import WorkItemService

router = APIRouter(prefix="/api/work-items", tags=["work-items"])


@router.get("", response_model=dict)
async def list_work_items(
    space_ref: str,
    staff_id: str,
    work_item_service: WorkItemService = Injected(WorkItemService),
) -> dict[str, Any]:
    try:
        items = await work_item_service.list_work_items(space_ref, staff_id)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return {"success": True, "data": [asdict(i) for i in items]}


@router.get("/detail", response_model=dict)
async def get_work_item(
    url: str,
    staff_id: str,
    work_item_service: WorkItemService = Injected(WorkItemService),
) -> dict[str, Any]:
    try:
        item = await work_item_service.get_work_item(WorkItemRef(url=url), staff_id)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return {"success": True, "data": asdict(item)}


@router.post("", response_model=dict)
async def create_work_item(
    body: dict[str, Any],
    work_item_service: WorkItemService = Injected(WorkItemService),
) -> dict[str, Any]:
    payload = dict(body)
    req = WorkItemCreate(
        staff_id=payload.pop("staffId", ""),
        space_ref=payload.pop("spaceRef", ""),
        subject=payload.pop("subject", ""),
        content=payload.pop("content", ""),
        item_type=payload.pop("itemType", "task"),
        priority=payload.pop("priority", "P2"),
        extra=payload,
    )
    try:
        item = await work_item_service.create_work_item(req)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return {"success": True, "data": asdict(item)}


__all__ = ["router"]
