"""File-system based resource API — thin HTTP adapter over ResourceFileService.

Every file operation (list / upload / download / preview / delete / mkdir) delegates
to :class:`ResourceFileService`, which addresses files by the logical
``workspace/<rel>`` namespace and is provider-agnostic — per-provider container/host
path composition lives in the device-filesystem plugin's mapper, and the dispatcher
picks the right plugin (arca / teclaw / baas incl. desktop / local) by the bot's
binding. This adapter only shapes requests/responses and maps errors to HTTP status.

Path sandboxing: ``path`` params are relative to the bot's workspace root; directory
traversal is rejected downstream.
"""
from __future__ import annotations

import logging
from typing import List as ListType, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.resources.schemas import (
    FileActionResponse,
    FileItem,
    FileListResponse,
    FileUploadResponse,
    PreviewData,
    PreviewResponse,
)
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_engine_for_bot
from agentclaw.community.core.services.resource_file_service import (
    ResourceFileService,
    is_readonly,
)
from agentclaw.community.core.devices.services.device_filesystem import FileTooLargeError
from agentclaw.community.di import Injected

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["resource-files"])


_MIME_TYPES = {
    'pdf': 'application/pdf', 'txt': 'text/plain', 'md': 'text/markdown',
    'json': 'application/json', 'jsonl': 'text/plain', 'yaml': 'application/yaml', 'yml': 'application/yaml',
    'xml': 'application/xml', 'html': 'text/html', 'htm': 'text/html',
    'css': 'text/css', 'js': 'application/javascript', 'csv': 'text/csv',
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'svg': 'image/svg+xml', 'mp4': 'video/mp4',
    'mp3': 'audio/mpeg', 'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'zip': 'application/zip', 'tar': 'application/x-tar', 'gz': 'application/gzip',
}
_INLINE_TYPES = {'pdf', 'txt', 'md', 'json', 'jsonl', 'xml', 'html', 'png', 'jpg', 'jpeg', 'gif', 'svg', 'mp4', 'mp3'}


# ---- Helpers ----


def _resolve_params(
    ctx: RequestContext,
    bot_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    owner_id: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> tuple:
    """Resolve (effective_owner_id, effective_bot_id, engine_type) for file ops.

    ``engine_type`` defaults to the bot's ``active_engine`` (looked up by bot_id) so
    the frontend needn't pass it. ``owner_id`` (collaborator access) falls back to the
    request user. (Also imported by the sibling search/zip router.)
    """
    effective_bot_id = bot_id or ctx.bot_id or "default"
    effective_owner_id = owner_id or ctx.user_id
    return (
        effective_owner_id,
        effective_bot_id,
        resolve_engine_for_bot(
            bot_id=effective_bot_id,
            owner_id=effective_owner_id,
            override=engine_type,
            bot_repo=bot_repo,
        ),
    )


def _content_headers(path: str) -> tuple[str, str]:
    """(media_type, content_disposition) for a download path.

    Single caller is ``download_file``; force ``attachment`` so the browser
    always saves the file instead of rendering text/json inline in the tab.
    (``preview_file`` returns JSON via ``PreviewResponse`` and never lands here,
    so the old ``inline`` branch for browsable types is gone.)
    """
    filename = path.rsplit('/', 1)[-1] if '/' in path else path
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    media_type = _MIME_TYPES.get(ext, 'application/octet-stream')
    return media_type, f"attachment; filename*=UTF-8''{quote(filename)}"


# ---- Endpoints ----


@router.get("", response_model=FileListResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,
))
async def list_files(
    path: str = Query("", description="Relative path from workspace root"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    publish_id: Optional[str] = Query(None, description="Publish ID for reading from published bot device"),
    device_uuid: Optional[str] = Query(None, description="Device UUID for multi-instance targeting; omitted → active instance"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> FileListResponse:
    """List directory contents at given path (default: workspace root)."""
    eid, ebid, eeng = _resolve_params(ctx, bot_id, engine_type, owner_id, bot_repo=bot_repo)
    try:
        items = await file_svc.list_dir(
            entity_id=eid, bot_id=ebid, engine_type=eeng, path=path, publish_id=publish_id,
            device_uuid=device_uuid,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FileListResponse(path=path, items=[FileItem(**i) for i in items])


@router.post("/upload", response_model=FileUploadResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
))
async def upload_files(
    files: ListType[UploadFile] = File(...),
    path: str = Query("", description="Target directory relative to workspace root"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> FileUploadResponse:
    """Upload file(s) to specified directory."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    eid, ebid, eeng = _resolve_params(ctx, bot_id, engine_type, owner_id, bot_repo=bot_repo)

    # any path separator in a filename means a directory upload (preserve structure)
    preserve = any('/' in (f.filename or '') for f in files)
    uploaded: list[FileItem] = []
    errors: list[dict] = []
    for file in files:
        try:
            result = await file_svc.upload_file(
                entity_id=eid, bot_id=ebid, engine_type=eeng,
                target_dir=path, filename=file.filename or "unnamed",
                data=await file.read(), preserve_structure=preserve,
            )
            uploaded.append(FileItem(**result))
        except ValueError as e:
            errors.append({"filename": file.filename, "error": str(e)})
        except Exception as e:
            logger.error(f"[upload_files] Failed: {file.filename}, {e}")
            errors.append({"filename": file.filename, "error": str(e)})

    return FileUploadResponse(success=len(errors) == 0, uploaded=uploaded,
                              errors=errors if errors else None)


@router.get("/download")
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,
))
async def download_file(
    path: str = Query(..., description="File relative path from workspace root"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    publish_id: Optional[str] = Query(None, description="Publish ID for reading from published bot device"),
    device_uuid: Optional[str] = Query(None, description="Device UUID for multi-instance targeting; omitted → active instance"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
):
    """Download file content as a streaming response."""
    eid, ebid, eeng = _resolve_params(ctx, bot_id, engine_type, owner_id, bot_repo=bot_repo)
    try:
        content = await file_svc.read_file(
            entity_id=eid, bot_id=ebid, engine_type=eeng, path=path,
            publish_id=publish_id, device_uuid=device_uuid, enforce_download_limit=True,
        )
    except FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if content is None:
        raise HTTPException(status_code=404, detail="File not found")

    media_type, content_disp = _content_headers(path)

    async def _iter():
        yield content

    return StreamingResponse(
        _iter(), media_type=media_type,
        headers={'Content-Disposition': content_disp, 'Content-Length': str(len(content))},
    )


@router.get("/preview", response_model=PreviewResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,
))
async def preview_file(
    path: str = Query(..., description="File relative path from workspace root"),
    max_size: int = Query(1024 * 1024, description="Max preview size in bytes"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    publish_id: Optional[str] = Query(None, description="Publish ID for reading from published bot device"),
    device_uuid: Optional[str] = Query(None, description="Device UUID for multi-instance targeting; omitted → active instance"),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> PreviewResponse:
    """Preview file content (text files only)."""
    eid, ebid, eeng = _resolve_params(ctx, bot_id, engine_type, owner_id, bot_repo=bot_repo)
    try:
        content = await file_svc.read_file(
            entity_id=eid, bot_id=ebid, engine_type=eeng, path=path,
            publish_id=publish_id, device_uuid=device_uuid,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if content is None:
        raise HTTPException(status_code=404, detail="File not found")
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail=f"File too large for preview (max {max_size} bytes)")
    return PreviewResponse(
        success=True,
        data=PreviewData(content=content.decode('utf-8', errors='replace'), size=len(content)),
    )


@router.delete("", response_model=FileActionResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
))
async def delete_file(
    path: str = Query(..., description="File/dir relative path from workspace root"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> FileActionResponse:
    """Delete a file or directory."""
    if is_readonly(path):
        raise HTTPException(status_code=403, detail="Cannot delete read-only file")

    eid, ebid, eeng = _resolve_params(ctx, bot_id, engine_type, owner_id, bot_repo=bot_repo)
    ok = await file_svc.delete(entity_id=eid, bot_id=ebid, engine_type=eeng, path=path)
    if not ok:
        raise HTTPException(status_code=404, detail="File not found")
    return FileActionResponse(success=True, message="Deleted")


@router.post("/mkdir", response_model=FileActionResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
))
async def create_directory(
    path: str = Form(..., description="Directory relative path from workspace root"),
    bot_id: Optional[str] = Query(None, description="Bot ID"),
    engine_type: Optional[str] = Query(None, description="Engine override; defaults to bot's active_engine"),
    owner_id: Optional[str] = Query(None, description="Bot owner ID. Required for collaborators."),
    ctx: RequestContext = Depends(get_request_context),
    bot_repo: BotRepository = Injected(BotRepository),
    file_svc: ResourceFileService = Injected(ResourceFileService),
) -> FileActionResponse:
    """Create a new directory."""
    eid, ebid, eeng = _resolve_params(ctx, bot_id, engine_type, owner_id, bot_repo=bot_repo)
    try:
        await file_svc.create_directory(entity_id=eid, bot_id=ebid, engine_type=eeng, path=path)
    except Exception as e:
        logger.error(f"[create_directory] Failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create directory: {str(e)}")
    return FileActionResponse(success=True, message=f"Directory created: {path}")
