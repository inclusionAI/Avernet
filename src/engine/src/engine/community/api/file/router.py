"""File router — dispatches every endpoint through ``EngineManager.file``.

Path translation + on-disk operations live in the engine's
``file`` plugin (e.g. ``engines/openclaw/file.OpenClawFileService`` rewrites
the legacy ``/aidesktop/...`` workspace prefix). The router only marshals
HTTP form data / multipart uploads, applies capability guards, and
maps plugin exceptions to HTTP status codes.
"""
from __future__ import annotations

import logging
import asyncio
import time
from typing import NoReturn

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse

from engine.community.api.caps import check_capability
from engine.community.api.file.schemas import (
    ListDirRequest,
    ReadFileRequest,
    RemovePathRequest,
    RmTreeRequest,
)
from engine.community.api.response import ApiResponse
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.kernel.file_count import FileCountError, file_count_request_id, redact_fields

log = logging.getLogger("api-file")

router = APIRouter(prefix="/api/file", tags=["file"])

_COUNT_STATUS = {
    "invalid_path": 400, "not_directory": 400, "path_not_found": 404,
    "path_forbidden": 403, "permission_denied": 403, "scan_timeout": 408,
    "directory_changed": 409, "busy": 503, "scan_failed": 500, "unsupported": 501,
}


@router.get("/count", response_model=ApiResponse)
async def count_files(
    path: str = Query(..., max_length=4096),
    request_id: str = Query(..., min_length=1, max_length=128),
) -> ApiResponse:
    from engine.community.manager import EngineManager

    started = time.monotonic()
    fields = {
        "system": "engine", "operation": "file_count", "direction": "inbound",
        "engine": EngineManager.get_instance().engine, "path": path,
        "request_id": request_id, "elapsed_ms": 0, "status": "started",
    }
    log.info("engine.file_count.request", extra={"fields": redact_fields(fields)})
    code = "scan_failed"
    correlation = file_count_request_id.set(request_id)
    try:
        warning = check_capability(Capability.FILE_LIST)
        result = await _file_plugin().count_files(path)
    except asyncio.CancelledError:
        fields.update(status="cancelled", error_code="cancelled", elapsed_ms=int((time.monotonic() - started) * 1000))
        log.info("engine.file_count.failure", extra={"fields": redact_fields(fields)})
        raise
    except FileCountError as error:
        code = error.code if error.code in _COUNT_STATUS else "scan_failed"
    except (CapabilityNotSupportedError, NotImplementedError):
        code = "unsupported"
    except HTTPException as error:
        code = "unsupported" if error.status_code == 501 else "scan_failed"
    except Exception:  # noqa: BLE001 — sanitized stable boundary error only
        pass
    else:
        data = {"path": result.path, "file_count": result.file_count, "elapsed_ms": result.elapsed_ms}
        fields.update(status="success", response=data, elapsed_ms=int((time.monotonic() - started) * 1000))
        log.info("engine.file_count.response", extra={"fields": redact_fields(fields)})
        return ApiResponse(success=True, data=data, warning=warning)
    finally:
        file_count_request_id.reset(correlation)
    fields.update(status="failed", error_code=code, file_count=None, elapsed_ms=int((time.monotonic() - started) * 1000))
    log.info("engine.file_count.failure", extra={"fields": redact_fields(fields)})
    raise HTTPException(status_code=_COUNT_STATUS[code], detail=code)


def _file_plugin():
    from engine.community.manager import EngineManager
    return EngineManager.get_instance().file


def _map_fs_error(e: Exception) -> NoReturn:
    """Translate plugin exceptions into HTTPException codes."""
    if isinstance(e, CapabilityNotSupportedError):
        raise HTTPException(status_code=501, detail=str(e)) from e
    if isinstance(e, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(e)) from e
    if isinstance(e, (FileExistsError, IsADirectoryError)):
        raise HTTPException(status_code=409, detail=str(e)) from e
    if isinstance(e, NotADirectoryError):
        raise HTTPException(status_code=400, detail=str(e)) from e
    if isinstance(e, ValueError):
        raise HTTPException(status_code=400, detail=str(e)) from e
    if isinstance(e, PermissionError):
        raise HTTPException(status_code=403, detail=str(e)) from e
    raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/upload", response_model=ApiResponse)
async def upload_file(
    file: UploadFile = File(...),
    target_path: str = Form(...),
) -> ApiResponse:
    warning = check_capability(Capability.FILE_UPLOAD)
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    try:
        content = await file.read()
        plugin = _file_plugin()
        result = await plugin.upload(target_path, content)
    except Exception as e:  # noqa: BLE001 — mapped to HTTP via helper
        _map_fs_error(e)
    finally:
        await file.close()

    return ApiResponse(
        success=True,
        data={
            "target_path": result.target_path,
            "size": result.size,
            "overwritten": result.overwritten,
        },
        message="文件已覆盖" if result.overwritten else "文件上传成功",
        warning=warning,
    )


@router.post("/read")
async def read_file(body: ReadFileRequest) -> Response:
    check_capability(Capability.FILE_READ)
    if not body.file_path.strip():
        return Response(content=b"", media_type="application/octet-stream")
    try:
        plugin = _file_plugin()
        content = await plugin.read(body.file_path)
    except Exception as e:  # noqa: BLE001
        _map_fs_error(e)

    def iterfile():
        chunk_size = 64 * 1024
        for i in range(0, len(content), chunk_size):
            yield content[i:i + chunk_size]

    return StreamingResponse(iterfile(), media_type="application/octet-stream")


@router.post("/remove", response_model=ApiResponse)
async def remove_path(body: RemovePathRequest) -> ApiResponse:
    warning = check_capability(Capability.FILE_DELETE)
    try:
        plugin = _file_plugin()
        result = await plugin.remove(body.target_path)
    except Exception as e:  # noqa: BLE001
        _map_fs_error(e)

    return ApiResponse(
        success=True,
        data={
            "target_path": result.target_path,
            "path_type": result.path_type,
        },
        message=f"{'文件' if result.path_type == 'file' else '目录'}删除成功",
        warning=warning,
    )


@router.post("/rmtree", response_model=ApiResponse)
async def remove_tree(body: RmTreeRequest) -> ApiResponse:
    warning = check_capability(Capability.FILE_DELETE)
    try:
        plugin = _file_plugin()
        final_path = await plugin.rmtree(body.target_path)
    except Exception as e:  # noqa: BLE001
        _map_fs_error(e)

    return ApiResponse(
        success=True,
        data={"target_path": final_path},
        message="目录删除成功",
        warning=warning,
    )


@router.post("/list", response_model=ApiResponse)
async def list_directory(body: ListDirRequest) -> ApiResponse:
    warning = check_capability(Capability.FILE_LIST)
    try:
        plugin = _file_plugin()
        result = await plugin.list_dir(body.dir_path, recursive=body.recursive)
    except Exception as e:  # noqa: BLE001
        _map_fs_error(e)

    files = [
        {
            "name": f.name,
            "path": f.path,
            "relative_path": f.relative_path,
            "size": f.size,
            "is_dir": f.is_dir,
        }
        for f in result.files
    ]
    return ApiResponse(
        success=True,
        data={
            "dir_path": result.dir_path,
            "recursive": result.recursive,
            "files": files,
            "total": len(files),
        },
        message="获取目录列表成功",
        warning=warning,
    )
