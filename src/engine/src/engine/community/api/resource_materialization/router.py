"""Thin HTTP adapter for Backend-triggered materialization."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator

from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
)
from engine.community.core.resource_materialization.service import (
    ResourceMaterializationService,
    ResourceNotMaterializedError,
)
from engine.community.di import Injected
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse

log = logging.getLogger("engine.resource_materialization.api")
router = APIRouter(prefix="/api/resource-materializations", tags=["resource-materializations"])


@router.post("")
async def create_resource_materialization(
    request: MaterializationRequest,
    background_tasks: BackgroundTasks,
    service: ResourceMaterializationService = Injected(ResourceMaterializationService),
) -> dict:
    """Accept a Backend task after BaaS proxypass authenticates the route.

    The endpoint is not browser-accessible. BaaS proxypass authenticates the
    container hop, and Backend authorizes the user and resource before it
    schedules this asynchronous task. A retry worker has no user IAM to carry.
    """

    background_tasks.add_task(service.materialize, request)
    log.info(
        "engine.resource_materialize.accept resource_id=%s task_version=%s transfer_hash=%s",
        request.resource_id,
        request.task_version,
        hashlib.sha256(request.transfer_id.encode("utf-8")).hexdigest()[:16],
    )
    return {
        "accepted": True,
        "task_id": request.task_id,
        "task_version": request.task_version,
    }


@router.get("/{resource_id}/content")
async def stream_resource_content(
    resource_id: str,
    disposition: str = Query("inline", pattern="^(inline|attachment)$"),
    service: ResourceMaterializationService = Injected(ResourceMaterializationService),
) -> StreamingResponse:
    """Stream manifest-controlled content over the Backend-only route."""
    try:
        content = await asyncio.to_thread(
            service.open_content,
            resource_id=resource_id,
            disposition=disposition,
        )
    except ResourceNotMaterializedError as exc:
        log.info("engine.resource_content.missing resource_id=%s", resource_id)
        raise HTTPException(status_code=409, detail="resource_not_materialized") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def body() -> AsyncIterator[bytes]:
        stream = await asyncio.to_thread(content.path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(stream.read, 1024 * 1024):
                yield chunk
        finally:
            await asyncio.to_thread(stream.close)

    return StreamingResponse(
        body(),
        media_type=content.media_type,
        headers={
            "Content-Length": str(content.size_bytes),
            "Content-Disposition": content.content_disposition,
        },
    )
