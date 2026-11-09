"""Service-Bot Phase-1 unified evaluation HTTP API."""

from __future__ import annotations

from typing import Sequence

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot.schemas import ApiResponse
from agentclaw.community.api.service_bot_eval import (
    ServiceBotEvalServiceProtocol,
)
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationConflictError,
    ServicePublicationNotFoundError,
    ServicePublicationUnsupportedError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.service_bot_eval import ServiceBotEvalFile

logger = get_logger()

router = APIRouter(prefix="/api/service-bot/eval", tags=["service-bot-eval"])

MAX_FILES = 100
MAX_SINGLE_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 128 * 1024 * 1024


def _actor(user: AuthenticatedUser) -> str:
    operator_id = user.staffId
    if not operator_id or operator_id == "anonymous":
        raise ServicePublicationConflictError("无法获取用户信息")
    return operator_id


async def _files(
    value: Sequence[UploadFile],
    *,
    file_kind: str,
    empty_message: str,
) -> list[ServiceBotEvalFile]:
    value = list(value)
    if not value:
        raise ServicePublicationConflictError(empty_message)
    files: list[ServiceBotEvalFile] = []
    for item in value:
        filename = item.filename or ""
        has_drive_letter = len(filename) >= 2 and filename[1] == ":" and filename[0].isalpha()
        if (
            not filename
            or "/" in filename
            or "\\" in filename
            or has_drive_letter
            or any(ord(ch) < 32 for ch in filename)
        ):
            raise ServicePublicationConflictError("评测材料文件名不合法")
        content = await item.read()
        if len(content) > MAX_SINGLE_FILE_BYTES:
            raise ServicePublicationConflictError("单个评测材料不能超过64MiB")
        files.append(
            ServiceBotEvalFile(
                filename=filename,
                content=content,
                content_type=item.content_type or "application/octet-stream",
                file_kind=file_kind,  # type: ignore[arg-type]
            )
        )
    return files


async def _upload_files(
    sample_files: Sequence[UploadFile],
    report_files: Sequence[UploadFile],
) -> list[ServiceBotEvalFile]:
    samples = await _files(
        sample_files,
        file_kind="sample",
        empty_message="评测样本材料不能为空",
    )
    reports = await _files(
        report_files,
        file_kind="report",
        empty_message="评测报告材料不能为空",
    )
    values = [*samples, *reports]
    if len(values) > MAX_FILES:
        raise ServicePublicationConflictError("样本和报告合计最多上传100个文件")
    if sum(len(file.content) for file in values) > MAX_TOTAL_FILE_BYTES:
        raise ServicePublicationConflictError("样本和报告合计不能超过128MiB")
    return values


@router.get(
    "/submissions/{submission_id}",
    response_model=ApiResponse,
    summary="查询评测提交结果",
)
async def get_submission(
    submission_id: str = Path(..., description="TeamClaw提交ID"),
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot owner ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> ApiResponse:
    """Read one pollable submission, resolving Bot context from the query identifiers."""
    try:
        result = await service.get_submission(
            bot_id,
            submission_id,
            actor_id=_actor(user),
            owner_id=owner_id,
        )
        return ApiResponse(success=True, data=result)
    except ServicePublicationNotFoundError:
        return ApiResponse(success=False, message="Bot不存在或无权访问", error_code=404, data=None)
    except ServicePublicationConflictError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=409, data=None)
    except Exception:
        logger.exception("[service_bot_eval] get submission failed bot=%s", bot_id)
        return ApiResponse(success=False, message="评测提交结果查询失败", error_code=500, data=None)


@router.get(
    "/{bot_id}/versions/{bot_version}/detail",
    response_model=ApiResponse,
    summary="查询评测详情",
)
async def detail(
    bot_id: str = Path(..., description="Bot ID"),
    bot_version: str = Path(..., description="服务Bot版本"),
    owner_id: str = Query(..., description="Bot owner ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> ApiResponse:
    try:
        result = await service.detail(
            bot_id,
            bot_version,
            actor_id=_actor(user),
            owner_id=owner_id,
        )
        return ApiResponse(success=True, data=result)
    except ServicePublicationNotFoundError:
        return ApiResponse(success=False, message="Bot不存在或无权访问", error_code=404, data=None)
    except ServicePublicationUnsupportedError:
        return ApiResponse(success=False, message="仅服务Bot支持统一评测", error_code=409, data=None)
    except ServicePublicationConflictError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=409, data=None)
    except Exception:
        logger.exception("[service_bot_eval] detail failed bot=%s", bot_id)
        return ApiResponse(success=False, message="评测详情加载失败", error_code=500, data=None)


@router.post(
    "/{bot_id}/versions/{bot_version}/simple-cert/precheck",
    response_model=ApiResponse,
    summary="发布推进门禁预校验",
)
async def precheck(
    bot_id: str = Path(..., description="Bot ID"),
    bot_version: str = Path(..., description="服务Bot版本"),
    owner_id: str = Query(..., description="Bot owner ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> ApiResponse:
    try:
        result = await service.precheck(
            bot_id,
            bot_version,
            actor_id=_actor(user),
            owner_id=owner_id,
        )
        return ApiResponse(success=True, data=result)
    except ServicePublicationNotFoundError:
        return ApiResponse(success=False, message="Bot不存在或无权访问", error_code=404, data=None)
    except ServicePublicationUnsupportedError:
        return ApiResponse(success=False, message="仅服务Bot支持发布门禁", error_code=409, data=None)
    except ServicePublicationConflictError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=409, data=None)
    except Exception:
        logger.exception("[service_bot_eval] precheck failed bot=%s", bot_id)
        return ApiResponse(success=False, message="发布门禁校验失败", error_code=500, data=None)


@router.post(
    "/{bot_id}/versions/{bot_version}/simple-cert/submissions",
    response_model=ApiResponse,
    summary="提交极简评测材料",
)
async def submit(
    bot_id: str = Path(..., description="Bot ID"),
    bot_version: str = Path(..., description="服务Bot版本"),
    owner_id: str = Query(..., description="Bot owner ID"),
    sample_files: list[UploadFile] = File(..., alias="sample_files"),
    report_files: list[UploadFile] = File(..., alias="report_files"),
    trigger_source: str = Form(default="manual_simple_cert"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> ApiResponse:
    try:
        result = await service.submit(
            bot_id,
            bot_version,
            actor_id=_actor(user),
            owner_id=owner_id,
            files=await _upload_files(sample_files, report_files),
            trigger_source=trigger_source,
        )
        return ApiResponse(success=True, data=result, message="评测数据已提交，等待认证结果")
    except ServicePublicationNotFoundError:
        return ApiResponse(success=False, message="Bot不存在或无权访问", error_code=404, data=None)
    except ServicePublicationUnsupportedError:
        return ApiResponse(success=False, message="仅服务Bot支持评测数据上报", error_code=409, data=None)
    except ServicePublicationConflictError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=409, data=None)
    except Exception:
        logger.exception("[service_bot_eval] submit failed bot=%s", bot_id)
        return ApiResponse(success=False, message="评测数据提交失败", error_code=500, data=None)


@router.get(
    "/{bot_id}/versions/{bot_version}/history",
    response_model=ApiResponse,
    summary="查询历史评测记录",
)
async def history(
    bot_id: str = Path(..., description="Bot ID"),
    bot_version: str = Path(..., description="服务Bot版本"),
    owner_id: str = Query(..., description="Bot owner ID"),
    page_no: int = Query(1, ge=1, le=100),
    page_size: int = Query(10, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> ApiResponse:
    try:
        result = await service.history(
            bot_id,
            bot_version,
            actor_id=_actor(user),
            owner_id=owner_id,
            page_no=page_no,
            page_size=page_size,
        )
        return ApiResponse(success=True, data=result)
    except ServicePublicationNotFoundError:
        return ApiResponse(success=False, message="Bot不存在或无权访问", error_code=404, data=None)
    except ServicePublicationUnsupportedError:
        return ApiResponse(success=False, message="仅服务Bot支持历史评测", error_code=409, data=None)
    except ServicePublicationConflictError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=409, data=None)
    except Exception:
        logger.exception("[service_bot_eval] history failed bot=%s", bot_id)
        return ApiResponse(success=False, message="历史评测记录加载失败", error_code=500, data=None)


@router.post(
    "/{bot_id}/versions/{bot_version}/simple-cert/sync",
    response_model=ApiResponse,
    summary="同步极简可信认证状态",
)
async def sync(
    bot_id: str = Path(..., description="Bot ID"),
    bot_version: str = Path(..., description="服务Bot版本"),
    owner_id: str = Query(..., description="Bot owner ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ServiceBotEvalServiceProtocol = Injected(ServiceBotEvalServiceProtocol),
) -> ApiResponse:
    try:
        result = await service.sync(
            bot_id,
            bot_version,
            actor_id=_actor(user),
            owner_id=owner_id,
        )
        return ApiResponse(success=True, data=result)
    except ServicePublicationNotFoundError:
        return ApiResponse(success=False, message="Bot不存在或无权访问", error_code=404, data=None)
    except ServicePublicationUnsupportedError:
        return ApiResponse(success=False, message="仅服务Bot支持认证状态同步", error_code=409, data=None)
    except ServicePublicationConflictError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=409, data=None)
    except Exception:
        logger.exception("[service_bot_eval] sync failed bot=%s", bot_id)
        return ApiResponse(success=False, message="认证状态同步失败", error_code=500, data=None)
