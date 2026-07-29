"""Thin HTTP adapter for Backend-triggered materialization."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
)
from engine.community.core.resource_materialization.service import (
    ResourceMaterializationService,
    ResourceNotMaterializedError,
)
from engine.community.di import Injected
from engine.community.plugin_api.auth_gate.protocol import AuthGateService

log = logging.getLogger("engine.resource_materialization.api")
router = APIRouter(prefix="/api/resource-materializations", tags=["resource-materializations"])


@router.post("")
async def create_resource_materialization(
    request: MaterializationRequest,
    background_tasks: BackgroundTasks,
    x_iam_token: Annotated[str | None, Header(alias="x-iam-token")] = None,
    auth_gate_service: AuthGateService = Injected(AuthGateService),
    service: ResourceMaterializationService = Injected(ResourceMaterializationService),
) -> dict:
    """Authenticate, accept, and run materialization after the response."""
    if not x_iam_token:
        raise HTTPException(status_code=401, detail="missing internal identity")
    try:
        verified = await auth_gate_service.verify(
            token=x_iam_token,
            content=f"materialize:{request.resource_id}",
            session_id=request.session_key_hash,
        )
    except Exception as exc:
        log.warning(
            "engine.resource_materialize.auth.fail resource_id=%s error_type=%s",
            request.resource_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=401, detail="internal identity verification failed") from exc
    if not verified.allowed:
        raise HTTPException(status_code=403, detail="internal identity denied")

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
    x_iam_token: Annotated[str | None, Header(alias="x-iam-token")] = None,
    auth_gate_service: AuthGateService = Injected(AuthGateService),
    service: ResourceMaterializationService = Injected(ResourceMaterializationService),
) -> StreamingResponse:
    """Internally authenticated, manifest-controlled content streaming."""
    if not x_iam_token:
        raise HTTPException(status_code=401, detail="missing internal identity")
    try:
        verified = await auth_gate_service.verify(
            token=x_iam_token,
            content=f"resource-content:{resource_id}",
            session_id=resource_id,
        )
    except Exception as exc:
        log.warning(
            "engine.resource_content.auth.fail resource_id=%s error_type=%s",
            resource_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=401,
            detail="internal identity verification failed",
        ) from exc
    if not verified.allowed:
        raise HTTPException(status_code=403, detail="internal identity denied")
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
