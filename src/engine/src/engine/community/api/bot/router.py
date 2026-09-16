"""
Bot 配置 API
"""
import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi_injector import Injected

from engine.community.api.bot.schemas import BotConfigRequest
from engine.community.api.response import ApiResponse
from engine.community.shared.credentials import get_credentials_service
from engine.community.core.publish_ignore.models import PublishIgnoreRequest, PublishIgnoreError
from engine.community.core.publish_ignore.protocol import PublishIgnoreService

router = APIRouter(prefix="/api/bot", tags=["bot"])
log = logging.getLogger("bot")


@router.post("/publish-ignore")
async def update_publish_ignore(
    request: PublishIgnoreRequest,
    service: PublishIgnoreService = Injected(PublishIgnoreService),
) -> ApiResponse:
    """Apply an authenticated Backend mutation to this exact running target."""
    started = time.monotonic()
    # COSEC: never log the signature or management secret, including failures.
    fields = request.model_dump(exclude={"authorization"})
    fields.update({"system": "engine", "direction": "inbound", "method": "POST",
                   "route": "/api/bot/publish-ignore"})
    log.info("engine.publish_ignore.request", extra={"operation_data": fields})
    try:
        result = await asyncio.to_thread(service.change, request)
    except PublishIgnoreError as exc:
        log.warning("engine.publish_ignore.failure", extra={"operation_data": fields,
                    "error_code": exc.code, "status_code": exc.status,
                    "error_type": type(exc).__name__, "error_message": exc.code,
                    "elapsed_ms": (time.monotonic() - started) * 1000})
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    except (OSError, UnicodeError) as exc:
        log.error("engine.publish_ignore.failure", extra={"operation_data": fields,
                  "error_type": type(exc).__name__, "error_code": "IGNORE_IO_ERROR",
                  "error_message": "Fixed-file operation failed", "errno": getattr(exc, "errno", None),
                  "status_code": 500, "elapsed_ms": (time.monotonic() - started) * 1000})
        raise HTTPException(status_code=500, detail="IGNORE_IO_ERROR") from exc
    log.info("engine.publish_ignore.success", extra={"operation_data": fields,
             "result": result, "status_code": 200,
             "elapsed_ms": (time.monotonic() - started) * 1000})
    return ApiResponse(success=True, data=result)


@router.post("/config")
async def update_bot_config(request: BotConfigRequest) -> ApiResponse:
    # 验证至少传了一个字段
    if request.role is None and request.visibility is None:
        raise HTTPException(status_code=400, detail="role 和 visibility 至少需要传一个")

    # 验证字段值
    if request.role is not None and request.role not in ("OWNER", "CALLER"):
        raise HTTPException(status_code=400, detail="role 必须是 OWNER 或 CALLER")

    if request.visibility is not None and request.visibility not in ("PRIVATE", "PUBLIC"):
        raise HTTPException(status_code=400, detail="visibility 必须是 PRIVATE 或 PUBLIC")

    # 构建更新字典
    updates = {}
    if request.role is not None:
        updates["ROLE"] = request.role
    if request.visibility is not None:
        updates["VISIBILITY"] = request.visibility

    # 更新文件
    service = get_credentials_service()
    service.update_fields(updates)

    # 返回更新后的配置
    creds = service.get_all()
    return ApiResponse(
        success=True,
        data={
            "role": creds.role,
            "visibility": creds.visibility,
        },
        message="更新成功"
    )
