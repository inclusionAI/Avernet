"""Direct, session-scoped file access through a Bot proxypass connection."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from engine.community.core.session_files.models import SessionFileError
from engine.community.core.session_files.service import SessionFileService
from engine.community.di import Injected
from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

log = logging.getLogger("engine.session_files.api")
router = APIRouter(prefix="/api/session-files", tags=["session-files"])


async def _authorize(
    *,
    auth_gate_service: AuthGateService,
    iam_token: str | None,
    session_key: str,
    operation: str,
    resource_id: str | None = None,
) -> None:
    if not iam_token:
        raise HTTPException(status_code=401, detail="missing_iam_token")
    try:
        result = await auth_gate_service.verify(
            token=iam_token,
            content=f"session-file:{operation}:{resource_id or 'session'}",
            session_id=session_key,
        )
    except Exception as exc:
        log.warning(
            "engine.session_files.auth.error operation=%s resource_id=%s error_type=%s",
            operation,
            resource_id,
            type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="session_file_auth_unavailable") from exc
    if not result.allowed:
        log.info(
            "engine.session_files.auth.denied operation=%s resource_id=%s",
            operation,
            resource_id,
        )
        raise HTTPException(status_code=403, detail="session_file_access_denied")


def _error(exc: SessionFileError) -> HTTPException:
    code = str(exc)
    if code == "resource_not_found":
        return HTTPException(status_code=404, detail=code)
    if code in {"resource_missing", "resource_changed"}:
        return HTTPException(status_code=409, detail=code)
    if code in {"manifest_unavailable", "workspace_root_not_configured"}:
        return HTTPException(status_code=503, detail=code)
    return HTTPException(status_code=400, detail=code)


@router.get("")
async def list_session_files(
    session_key: str = Query(alias="sessionKey", min_length=1, max_length=2048),
    x_iam_token: str | None = Header(default=None, alias="x-iam-token"),
    auth_gate_service: AuthGateService = Injected(AuthGateService),  # noqa: B008
    service: SessionFileService = Injected(SessionFileService),  # noqa: B008
) -> dict:
    await _authorize(
        auth_gate_service=auth_gate_service,
        iam_token=x_iam_token,
        session_key=session_key,
        operation="list",
    )
    try:
        files = await asyncio.to_thread(service.list_files, session_key=session_key)
    except SessionFileError as exc:
        raise _error(exc) from exc
    return {"files": [item.as_dict() for item in files]}


@router.get("/{resource_id}/content")
async def stream_session_file_content(
    resource_id: str,
    session_key: str = Query(alias="sessionKey", min_length=1, max_length=2048),
    disposition: str = Query("inline", pattern="^(inline|attachment)$"),
    x_iam_token: str | None = Header(default=None, alias="x-iam-token"),
    auth_gate_service: AuthGateService = Injected(AuthGateService),  # noqa: B008
    service: SessionFileService = Injected(SessionFileService),  # noqa: B008
) -> StreamingResponse:
    await _authorize(
        auth_gate_service=auth_gate_service,
        iam_token=x_iam_token,
        session_key=session_key,
        operation="content",
        resource_id=resource_id,
    )
    try:
        content = await asyncio.to_thread(
            service.open_content,
            session_key=session_key,
            resource_id=resource_id,
            disposition=disposition,
        )
    except SessionFileError as exc:
        raise _error(exc) from exc

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


@router.delete("/{resource_id}")
async def delete_session_file(
    resource_id: str,
    session_key: str = Query(alias="sessionKey", min_length=1, max_length=2048),
    x_iam_token: str | None = Header(default=None, alias="x-iam-token"),
    auth_gate_service: AuthGateService = Injected(AuthGateService),  # noqa: B008
    service: SessionFileService = Injected(SessionFileService),  # noqa: B008
) -> dict:
    await _authorize(
        auth_gate_service=auth_gate_service,
        iam_token=x_iam_token,
        session_key=session_key,
        operation="delete",
        resource_id=resource_id,
    )
    try:
        await asyncio.to_thread(
            service.delete_file,
            session_key=session_key,
            resource_id=resource_id,
        )
    except SessionFileError as exc:
        raise _error(exc) from exc
    return {"resource_id": resource_id, "deleted": True}
