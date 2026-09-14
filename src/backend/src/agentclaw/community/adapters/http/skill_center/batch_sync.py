"""Legacy SC batch routes.

The old batch-sync writer and unsafe cross-system batch-delete operation are
permanently retired.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/skill-center", tags=["skill-center"])


_BATCH_SYNC_RETIRED = (
    "Legacy batch sync is retired; use "
    "POST /openapi/v1/bots/market/skill-center/sync"
)
_BATCH_DELETE_RETIRED = (
    "Legacy batch delete is retired; remove Skill references and use the "
    "governed Skill lifecycle APIs"
)


def _retired_batch_sync() -> None:
    raise HTTPException(status_code=410, detail=_BATCH_SYNC_RETIRED)


def _retired_batch_delete() -> None:
    raise HTTPException(status_code=410, detail=_BATCH_DELETE_RETIRED)


@router.post("/batch-sync", deprecated=True)
async def batch_sync_post() -> None:
    _retired_batch_sync()


@router.get("/batch-sync", deprecated=True)
async def batch_sync_get() -> None:
    _retired_batch_sync()


@router.get("/batch-sync/status/{task_id}", deprecated=True)
async def get_batch_sync_status(task_id: str) -> None:
    del task_id
    _retired_batch_sync()


@router.get("/batch-sync/report/{task_id}", deprecated=True)
async def get_batch_sync_report(task_id: str) -> None:
    del task_id
    _retired_batch_sync()


@router.post("/batch-delete", deprecated=True)
async def batch_delete_post() -> None:
    _retired_batch_delete()


@router.get("/batch-delete", deprecated=True)
async def batch_delete_get() -> None:
    _retired_batch_delete()


@router.get("/batch-delete/status/{task_id}", deprecated=True)
async def get_batch_delete_status(task_id: str) -> None:
    del task_id
    _retired_batch_delete()


@router.get("/batch-delete/report/{task_id}", deprecated=True)
async def get_batch_delete_report(task_id: str) -> None:
    del task_id
    _retired_batch_delete()
