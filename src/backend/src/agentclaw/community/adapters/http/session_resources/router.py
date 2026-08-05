"""Thin authenticated HTTP adapters for session resources."""
from __future__ import annotations

from collections.abc import AsyncIterator
import hashlib
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.session_resources.schemas import (
    MaterializedCallbackRequest,
    ReferenceRequest,
    UploadCompleteRequest,
    UploadIntentRequest,
)
from agentclaw.community.api.session_resource_service import (
    SessionResourceServiceProtocol,
)
from agentclaw.community.core.session_resources.baas_client import (
    SessionFileUpstreamUnavailableError,
)
from agentclaw.community.core.session_resources.types import SessionResourceRecord
from agentclaw.community.di import Injected

router = APIRouter(prefix="/api/session-resources", tags=["session-resources"])
internal_router = APIRouter(prefix="/internal/session-resources", tags=["session-resources-internal"])


def _resource(record: SessionResourceRecord) -> dict:
    return {
        "resource_id": record.resource_id,
        "display_name": record.display_name,
        "status": record.status.value,
        "engine_type": record.engine_type,
        "size_bytes": record.size_bytes,
        "content_hash": record.client_content_hash,
        "task_version": record.task_version,
        "error_code": record.error_code,
    }


def _domain_error(exc: ValueError) -> HTTPException:
    code = str(exc)
    if isinstance(exc, SessionFileUpstreamUnavailableError):
        return HTTPException(status_code=502, detail=code)
    if code == "resource_not_found":
        return HTTPException(status_code=404, detail=code)
    if code in {"target_bot_access_denied"}:
        return HTTPException(status_code=403, detail=code)
    if code in {
        "bot_device_unavailable",
        "materialize_state_conflict",
        "transfer_id_mismatch",
    }:
        return HTTPException(status_code=409, detail=code)
    if code in {"resource_not_ready", "resource_materializing", "resource_missing", "resource_changed"}:
        return HTTPException(status_code=409, detail=code)
    if code == "engine_content_unavailable":
        return HTTPException(status_code=502, detail=code)
    return HTTPException(status_code=400, detail=code)


def _safe_content_headers(headers: object) -> dict[str, str]:
    if not hasattr(headers, "items"):
        return {"Content-Type": "application/octet-stream"}
    safe: dict[str, str] = {}
    allowed = {"content-type", "content-length", "content-disposition"}
    for key, value in headers.items():
        normalized = str(key).lower()
        if normalized not in allowed or not isinstance(value, str):
            continue
        if "\r" in value or "\n" in value:
            continue
        if normalized == "content-length" and not value.isdecimal():
            continue
        safe["-".join(part.capitalize() for part in normalized.split("-"))] = value
    safe.setdefault("Content-Type", "application/octet-stream")
    return safe


@router.post("/upload-intents")
async def create_upload_intents(
    body: UploadIntentRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    files = []
    try:
        for item in body.files:
            intent = service.create_upload_intent(
                owner_id=user.staffId,
                bot_id=body.bot_id,
                session_key=body.session_key,
                scope_type=body.scope_type,
                engine_type=body.engine_type,
                filename=item.filename,
                target_entity_id=body.target_entity_id,
                binding_id=body.binding_id,
                size_bytes=item.size_bytes,
                content_hash=item.content_hash,
            )
            files.append(
                {
                    **_resource(intent.resource),
                    "upload_url": intent.grant.upload_url,
                    "transfer_id": intent.grant.transfer_id,
                    "upload_type": intent.grant.upload_type,
                    "http_method": intent.grant.http_method,
                    "expires_at": intent.grant.expires_at,
                    "upload_session_id": intent.grant.upload_session_id,
                    "part_size": intent.grant.part_size,
                    "part_count": intent.grant.part_count,
                    "parts": intent.grant.parts,
                }
            )
    except ValueError as exc:
        raise _domain_error(exc) from exc
    return {"files": files}


@router.post("/upload-complete")
async def upload_complete(
    body: UploadCompleteRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    try:
        record = service.complete_upload(
            owner_id=user.staffId,
            bot_id=body.bot_id,
            session_key=body.session_key,
            resource_id=body.resource_id,
            transfer_id=body.transfer_id,
        )
    except ValueError as exc:
        raise _domain_error(exc) from exc
    return _resource(record)


@router.get("/{resource_id}/materialize-status")
async def materialize_status(
    resource_id: str,
    bot_id: str,
    session_key: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    try:
        return _resource(
            service.get_status(
                owner_id=user.staffId,
                bot_id=bot_id,
                session_key=session_key,
                resource_id=resource_id,
            )
        )
    except ValueError as exc:
        raise _domain_error(exc) from exc


@router.get("/pending")
async def list_pending_session_resources(
    bot_id: str,
    session_key: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    records = service.list_pending(
        owner_id=user.staffId,
        bot_id=bot_id,
        session_key=session_key,
    )
    return {"files": [_resource(record) for record in records]}


@router.get("", deprecated=True)
async def list_session_resources(
    bot_id: str,
    session_key: str,
    referable: bool = Query(False),
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    records = service.list_resources(
        owner_id=user.staffId,
        bot_id=bot_id,
        session_key=session_key,
        ready_only=referable,
    )
    return {"files": [_resource(record) for record in records]}


@router.get("/referable-files", deprecated=True)
async def referable_files(
    bot_id: str,
    session_key: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    records = service.list_resources(
        owner_id=user.staffId,
        bot_id=bot_id,
        session_key=session_key,
        ready_only=True,
    )
    return {"files": [_resource(record) for record in records]}


@router.post("/{resource_id}/reference", deprecated=True)
async def create_reference(
    resource_id: str,
    body: ReferenceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    try:
        reference = service.reference(
            owner_id=user.staffId,
            bot_id=body.bot_id,
            session_key=body.session_key,
            resource_id=resource_id,
        )
    except ValueError as exc:
        raise _domain_error(exc) from exc
    return {**reference, "insert_id": body.insert_id}


@router.get("/{resource_id}/content", deprecated=True)
async def stream_content(
    resource_id: str,
    bot_id: str,
    session_key: str,
    disposition: str = Query("inline", pattern="^(inline|attachment)$"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> StreamingResponse:
    try:
        record, upstream = await service.open_content(
            owner_id=user.staffId,
            bot_id=bot_id,
            session_key=session_key,
            resource_id=resource_id,
            disposition=disposition,
        )
    except ValueError as exc:
        raise _domain_error(exc) from exc

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.body:
                if isinstance(chunk, bytes):
                    yield chunk
        finally:
            await upstream.close()

    headers = _safe_content_headers(upstream.headers)
    headers.setdefault("Content-Disposition", f'{disposition}; filename="{record.filename}"')
    return StreamingResponse(body(), headers=headers)


@router.delete("/{resource_id}", deprecated=True)
async def delete_resource(
    resource_id: str,
    bot_id: str,
    session_key: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    try:
        record = service.delete(
            owner_id=user.staffId,
            bot_id=bot_id,
            session_key=session_key,
            resource_id=resource_id,
        )
    except ValueError as exc:
        raise _domain_error(exc) from exc
    return _resource(record)


@internal_router.post("/{resource_id}/materialized")
async def materialized_callback(
    resource_id: str,
    body: MaterializedCallbackRequest,
    x_materialization_task_id: str | None = Header(
        default=None,
        alias="x-materialization-task-id",
    ),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    # COSEC: the 128-bit task id is an unguessable single-task capability;
    # constant-time compare prevents a callback from probing valid prefixes.
    if not x_materialization_task_id or not secrets.compare_digest(
        x_materialization_task_id,
        body.task_id,
    ):
        raise HTTPException(status_code=401, detail="invalid materialization capability")
    materialized_ref = None
    if body.ready:
        materialized_ref = {
            "relative_path": body.relative_path,
            "path_hash": (
                hashlib.sha256(
                    (body.canonical_bot_absolute_path or "").encode("utf-8")
                ).hexdigest()
                if body.canonical_bot_absolute_path
                else None
            ),
            "size_bytes": body.size_bytes,
            "content_hash": body.content_hash,
        }
    try:
        result = service.materialized_callback(
            resource_id=resource_id,
            transfer_id=body.transfer_id,
            task_id=body.task_id,
            task_version=body.task_version,
            ready=body.ready,
            materialized_ref=materialized_ref,
            error_code=body.error_code,
        )
    except ValueError as exc:
        raise _domain_error(exc) from exc
    return {"applied": result is not None, "status": result.status.value if result else None}
