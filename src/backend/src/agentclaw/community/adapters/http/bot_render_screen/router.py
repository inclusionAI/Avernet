"""Bot Render Screen Router — 第四屏 CDN 配置 API。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from agentclaw.community.adapters.http.bot_render_screen.schemas import (
    CreateRenderScreenRequest,
    RenderScreenApiResponse,
    RenderScreenResponse,
    UpdateRenderScreenRequest,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user as _get_current_user
from agentclaw.community.adapters.http.bot_render_screen.dependencies import get_render_screen_service
from agentclaw.community.core.bot_management.render_screen.models import RenderScreenRecord
from agentclaw.community.api.render_screen_service import RenderScreenServiceProtocol
from agentclaw.community.log import get_logger


logger = get_logger()

get_current_user = _get_current_user

router = APIRouter(prefix="/api/bot-render-screens", tags=["bot-render-screens"])

logger.info("[RenderScreen] Router registered: prefix=%s", router.prefix)


def record_to_response(record: RenderScreenRecord) -> RenderScreenResponse:
    """RenderScreenRecord → RenderScreenResponse。"""
    return RenderScreenResponse(
        id=record.id,
        bot_id=record.bot_id,
        owner_id=record.owner_id,
        name=record.name,
        cdn_url=record.cdn_url,
        creator_id=record.creator_id,
        gmt_create=record.gmt_create.isoformat() if record.gmt_create else None,
        gmt_modified=record.gmt_modified.isoformat() if record.gmt_modified else None,
    )


@router.get("", response_model=RenderScreenApiResponse)
async def list_render_screens(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: Optional[str] = Query(
        None,
        description="Bot 归属者工号（兼容字段；coding bot 场景仅作历史兼容，不再作为共享作用域主键）",
    ),
    service: RenderScreenServiceProtocol = Depends(get_render_screen_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> RenderScreenApiResponse:
    """查询 Bot 的 CDN 链接列表。

    副屏 CDN 配置（库名 → CDN URL 映射）是非敏感资源。协作群/分享页的查看者
    并非 Bot 归属者，若强制 owner/协作者校验会导致群聊等场景查不到副屏配置，
    副屏渲染报「组件库不存在」。故读接口不做身份校验：coding bot 按 bot 维度
    返回全部配置，普通 bot 按 owner_id（或当前用户兜底）过滤。
    写操作（POST/PUT/DELETE）仍走 authorize_* 校验，读放开不影响写权限。
    """
    user_id = user.staffId
    try:
        records = service.list_render_screens(
            bot_id=bot_id,
            owner_id=owner_id,
            current_user_id=user_id,
        )
    except Exception as e:
        logger.exception("[RenderScreen] list_render_screens error: %s", e)
        return RenderScreenApiResponse(success=False, message=str(e), error_code=500, data=None)
    return RenderScreenApiResponse(
        success=True,
        data=[record_to_response(r).model_dump() for r in records],
        message="查询成功",
    )


@router.post("", response_model=RenderScreenApiResponse)
async def create_render_screen(
    request: CreateRenderScreenRequest,
    service: RenderScreenServiceProtocol = Depends(get_render_screen_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> RenderScreenApiResponse:
    """添加 CDN 链接。"""
    user_id = user.staffId
    try:
        service.authorize_render_screen_bot(bot_id=request.bot_id, user_id=user_id)
        record_id = service.create_render_screen(
            bot_id=request.bot_id,
            owner_id=user_id,
            name=request.name,
            cdn_url=request.cdn_url,
            creator_id=user_id,
            current_user_id=user_id,
        )
    except PermissionError as e:
        return RenderScreenApiResponse(success=False, message=str(e), error_code=403, data=None)
    except ValueError as e:
        return RenderScreenApiResponse(success=False, message=str(e), error_code=409, data=None)

    return RenderScreenApiResponse(
        success=True,
        data={"id": record_id},
        message="创建成功",
    )


@router.put("/{record_id}", response_model=RenderScreenApiResponse)
async def update_render_screen(
    record_id: int,
    request: UpdateRenderScreenRequest,
    service: RenderScreenServiceProtocol = Depends(get_render_screen_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> RenderScreenApiResponse:
    """更新 CDN 链接。"""
    user_id = user.staffId

    try:
        service.authorize_render_screen_record(record_id=record_id, user_id=user_id)
    except ValueError:
        return RenderScreenApiResponse(success=False, message="CDN 配置不存在", error_code=404, data=None)
    except PermissionError as e:
        return RenderScreenApiResponse(success=False, message=str(e), error_code=403, data=None)

    try:
        service.update_render_screen(
            record_id=record_id,
            name=request.name,
            cdn_url=request.cdn_url,
        )
    except ValueError as e:
        return RenderScreenApiResponse(success=False, message=str(e), error_code=409, data=None)

    return RenderScreenApiResponse(
        success=True,
        data={"id": record_id},
        message="更新成功",
    )


@router.delete("/{record_id}", response_model=RenderScreenApiResponse)
async def delete_render_screen(
    record_id: int,
    service: RenderScreenServiceProtocol = Depends(get_render_screen_service),
    user: AuthenticatedUser = Depends(get_current_user),
) -> RenderScreenApiResponse:
    """删除 CDN 链接（软删除）。"""
    user_id = user.staffId

    try:
        service.authorize_render_screen_record(record_id=record_id, user_id=user_id)
    except ValueError:
        return RenderScreenApiResponse(success=False, message="CDN 配置不存在", error_code=404, data=None)
    except PermissionError as e:
        return RenderScreenApiResponse(success=False, message=str(e), error_code=403, data=None)

    service.delete_render_screen(record_id=record_id)

    return RenderScreenApiResponse(
        success=True,
        data={"id": record_id},
        message="删除成功",
    )
