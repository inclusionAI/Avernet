"""Direct, session-scoped file access through a trusted Bot proxypass."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from engine.community.core.session_files.export_service import (
    EXPORT_POLL_SECONDS,
    SessionFileExportService,
)
from engine.community.core.session_files.models import SessionFileError
from engine.community.core.session_files.service import SessionFileService
from engine.community.di import Injected

log = logging.getLogger("engine.session_files.api")
router = APIRouter(prefix="/api/session-files", tags=["session-files"])
_MAX_DIRECT_CONTENT_BYTES = 30 * 1024 * 1024


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
    service: SessionFileService = Injected(SessionFileService),  # noqa: B008
) -> dict:
    log.info("engine.session_files.proxypass_access operation=list")
    try:
        files = await asyncio.to_thread(service.list_files, session_key=session_key)
    except SessionFileError as exc:
        raise _error(exc) from exc
    return {"files": [item.as_dict() for item in files]}


@router.get("/{resource_id}/content", response_model=None)
async def stream_session_file_content(
    resource_id: str,
    session_key: str = Query(alias="sessionKey", min_length=1, max_length=2048),
    disposition: str = Query("inline", pattern="^(inline|attachment)$"),
    service: SessionFileService = Injected(SessionFileService),  # noqa: B008
    export_service: SessionFileExportService = Injected(SessionFileExportService),  # noqa: B008
) -> Response:
    log.info(
        "engine.session_files.proxypass_access operation=content resource_id=%s",
        resource_id,
    )
    if disposition == "attachment":
        try:
            source = await asyncio.to_thread(
                service.prepare_export_source,
                session_key=session_key,
                resource_id=resource_id,
            )
        except SessionFileError as exc:
            raise _error(exc) from exc
        if source.size_bytes > _MAX_DIRECT_CONTENT_BYTES:
            result = await export_service.request_download(
                source=source,
                session_key=session_key,
            )
            if result.state == "preparing":
                return JSONResponse(
                    status_code=202,
                    content={
                        "status": "preparing_download",
                        "retry_after_seconds": EXPORT_POLL_SECONDS,
                    },
                    headers={
                        "Retry-After": str(EXPORT_POLL_SECONDS),
                        "Cache-Control": "no-store",
                    },
                )
            if result.state == "failed":
                if result.error_code in {"resource_missing", "resource_changed"}:
                    raise _error(SessionFileError(result.error_code))
                status_code = {
                    "file_export_unavailable": 503,
                    "file_export_timeout": 504,
                }.get(result.error_code, 502)
                raise HTTPException(status_code=status_code, detail=result.error_code)
            if result.download is None:
                raise HTTPException(status_code=502, detail="file_export_failed")
            return JSONResponse(
                content={
                    "status": "ready",
                    "delivery": "external_url",
                    "download_url": result.download.download_url,
                    "expires_at": result.download.expires_at,
                    "filename": result.download.filename,
                    "size_bytes": result.download.size_bytes,
                },
                headers={"Cache-Control": "no-store"},
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
    if content.size_bytes > _MAX_DIRECT_CONTENT_BYTES:
        raise HTTPException(status_code=413, detail="resource_preview_too_large")

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
    service: SessionFileService = Injected(SessionFileService),  # noqa: B008
) -> dict:
    log.info(
        "engine.session_files.proxypass_access operation=delete resource_id=%s",
        resource_id,
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
