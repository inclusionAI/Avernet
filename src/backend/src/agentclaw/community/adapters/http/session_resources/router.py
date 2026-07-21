"""Thin authenticated HTTP adapters for session resources."""
from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query

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
    if code == "resource_not_found":
        return HTTPException(status_code=404, detail=code)
    if code in {"materialize_state_conflict", "transfer_id_mismatch"}:
        return HTTPException(status_code=409, detail=code)
    return HTTPException(status_code=400, detail=code)


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
                size_bytes=item.size_bytes,
                content_hash=item.content_hash,
            )
            files.append(
                {
                    **_resource(intent.resource),
                    "upload_url": intent.grant.upload_url,
                    "transfer_id": intent.grant.transfer_id,
                    "expires_at": intent.grant.expires_at,
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


@router.get("")
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


@router.get("/referable-files")
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


@router.post("/{resource_id}/reference")
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


@router.get("/{resource_id}/download-url")
@router.get("/{resource_id}/preview")
async def create_download_or_preview(
    resource_id: str,
    bot_id: str,
    session_key: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SessionResourceServiceProtocol = Injected(SessionResourceServiceProtocol),
) -> dict:
    try:
        grant = service.create_download_grant(
            owner_id=user.staffId,
            bot_id=bot_id,
            session_key=session_key,
            resource_id=resource_id,
        )
    except ValueError as exc:
        raise _domain_error(exc) from exc
    return grant.__dict__


@router.delete("/{resource_id}")
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
