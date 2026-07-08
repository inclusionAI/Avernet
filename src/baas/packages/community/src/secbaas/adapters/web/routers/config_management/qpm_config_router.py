"""Bot QPM 配置 CRUD REST API routes.

Endpoints:
- GET /api/v1/bot-qpm - List all QPM configs for current env
- GET /api/v1/bot-qpm/{bot_id} - Get QPM config by bot_id
- POST /api/v1/bot-qpm - Create or update QPM config (upsert)
- PUT /api/v1/bot-qpm/{bot_id} - Update QPM config
- DELETE /api/v1/bot-qpm/{bot_id} - Delete QPM config
"""

from datetime import datetime
from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from secbaas.api import ApiResponse, SuccessResponse
from secbaas.api.bot_qpm import BotQpmManageService
from secbaas.bootstrap import ApplicationContainer, Provide

router = APIRouter(prefix="/api/v1/bot-qpm", tags=["Bot QPM 配置管理"])


class BotQpmResponse(BaseModel):
    id: int
    bot_id: str
    qpm: int
    env: str | None
    gmt_create: datetime | None
    gmt_modified: datetime | None


class BotQpmListResponse(BaseModel):
    items: list[BotQpmResponse]
    total: int


class BotQpmUpsertRequest(BaseModel):
    bot_id: str = Field(..., min_length=1, max_length=128, description="Bot ID")
    qpm: int = Field(..., ge=1, le=100000, description="每分钟请求数上限")


class BotQpmUpdateRequest(BaseModel):
    qpm: int = Field(..., ge=1, le=100000, description="每分钟请求数上限")


def _to_qpm_response(item) -> BotQpmResponse:
    return BotQpmResponse(
        id=item.id,
        bot_id=item.bot_id,
        qpm=item.qpm,
        env=item.env,
        gmt_create=item.gmt_create,
        gmt_modified=item.gmt_modified,
    )


@router.get("", response_model=ApiResponse[BotQpmListResponse])
@inject
async def list_qpm_configs(
    service: BotQpmManageService = Depends(
        Provide[ApplicationContainer.services.bot_qpm_manage_service]
    ),
) -> ApiResponse[BotQpmListResponse]:
    """列出当前 env 下所有 bot 的 QPM 配置。"""
    result = service.list_configs()
    items = [_to_qpm_response(i) for i in result.items]
    return ApiResponse(data=BotQpmListResponse(items=items, total=result.total))


@router.get("/{bot_id}", response_model=ApiResponse[BotQpmResponse])
@inject
async def get_qpm_config(
    bot_id: Annotated[str, Path(description="Bot ID")],
    service: BotQpmManageService = Depends(
        Provide[ApplicationContainer.services.bot_qpm_manage_service]
    ),
) -> ApiResponse[BotQpmResponse]:
    """查询单个 bot 的 QPM 配置。"""
    item = service.get_config(bot_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "QPM_CONFIG_NOT_FOUND",
                "message": f"QPM config not found for bot_id: {bot_id}",
            },
        )
    return ApiResponse(data=_to_qpm_response(item))


@router.post("", status_code=201, response_model=ApiResponse[BotQpmResponse])
@inject
async def upsert_qpm_config(
    request: BotQpmUpsertRequest,
    service: BotQpmManageService = Depends(
        Provide[ApplicationContainer.services.bot_qpm_manage_service]
    ),
) -> ApiResponse[BotQpmResponse]:
    """创建或更新 bot 的 QPM 配置（upsert 语义）。"""
    item = service.upsert_config(bot_id=request.bot_id, qpm=request.qpm)
    return ApiResponse(data=_to_qpm_response(item))


@router.put("/{bot_id}", response_model=ApiResponse[BotQpmResponse])
@inject
async def update_qpm_config(
    bot_id: Annotated[str, Path(description="Bot ID")],
    request: BotQpmUpdateRequest,
    service: BotQpmManageService = Depends(
        Provide[ApplicationContainer.services.bot_qpm_manage_service]
    ),
) -> ApiResponse[BotQpmResponse]:
    """更新 bot 的 QPM 配置。不存在则返回 404。"""
    item = service.update_config(bot_id=bot_id, qpm=request.qpm)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "QPM_CONFIG_NOT_FOUND",
                "message": f"QPM config not found for bot_id: {bot_id}",
            },
        )
    return ApiResponse(data=_to_qpm_response(item))


@router.delete("/{bot_id}", response_model=ApiResponse[SuccessResponse])
@inject
async def delete_qpm_config(
    bot_id: Annotated[str, Path(description="Bot ID")],
    service: BotQpmManageService = Depends(
        Provide[ApplicationContainer.services.bot_qpm_manage_service]
    ),
) -> ApiResponse[SuccessResponse]:
    """删除 bot 的 QPM 配置。"""
    success = service.delete_config(bot_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "QPM_CONFIG_NOT_FOUND",
                "message": f"QPM config not found for bot_id: {bot_id}",
            },
        )
    return ApiResponse(data=SuccessResponse(message="QPM config deleted"))
