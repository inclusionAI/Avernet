"""Bot public API routes.

提供 Bot 公开相关的接口：
- POST /{bot_id}/public - 公开/取消公开 Bot
- GET /search/by-conditions - 根据条件分页查询 Bot
- GET /search/by-keyword - 根据关键词分页查询 Bot
"""
from typing import Optional

from fastapi import APIRouter, Query, Path, Depends

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.bot_public.services.bot_public_service import (
    BotPublicServiceError,
    BotNotFoundError,
    BotPermissionError,
    OperatorContext,
)
from agentclaw.community.adapters.http.bot_public.schemas import BotPublicRequest, ApiResponse
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    with_interceptors,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/bots", tags=["bot-public"])


@router.post("/{bot_id}/public", response_model=ApiResponse)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$req.owner_id",
))
async def public_bot(
    bot_id: str = Path(..., description="Bot ID"),
    req: BotPublicRequest = ...,
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> ApiResponse:
    """
    公开/取消公开 Bot。

    POST /api/bots/{bot_id}/public
    Body: {
        "public": "0" | "1",
        "permission_owner": "caller" | "owner",
        "owner_id": "可选，协作者场景下指定Bot所有者ID"
    }

    - permission_owner="caller": 直接更新 public 字段
    - permission_owner="owner":  需要发起审批，等待回调后更新
    - owner_id: 可选，协作者操作时指定Bot所有者ID，未指定则使用当前用户ID
    """
    try:
        user_id = user.staffId
        nick_name = user.nickName or user_id

        if not user_id or user_id == "anonymous":
            return ApiResponse(success=False, message="无法获取用户信息", error_code=400, data=None)

        # 使用请求中的 owner_id，如果没有则使用当前用户 ID
        effective_owner_id = req.owner_id if req.owner_id else user_id

        operator = OperatorContext(
            staff_id=user_id,
            staff=user_id,
            nick_name=nick_name or user_id,
            operator_name=nick_name or user_id,
            tenant_id="default",
        )
        result = service.public_bot(
            bot_id=bot_id,
            owner_id=effective_owner_id,
            public=req.public,
            permission_owner=req.permission_owner,
            friend_approval=req.friend_approval,
            operator=operator,
        )

        return ApiResponse(success=True, data=result, message="操作成功")

    except BotNotFoundError:
        return ApiResponse(success=False, message=f"Bot不存在: {bot_id}", error_code=404, data=None)
    except BotPermissionError as e:
        logger.warning(f"[bot_router.public_bot] Permission denied: {e}")
        return ApiResponse(success=False, message=f"权限不足: {str(e)}", error_code=403, data=None)
    except BotPublicServiceError as e:
        logger.error(f"[bot_router.public_bot] Bot service error: {e}")
        return ApiResponse(success=False, message=f"公开Bot失败: {str(e)}", error_code=500, data=None)
    except Exception as e:
        logger.error(f"[bot_router.public_bot] Unexpected error: {e}")
        return ApiResponse(success=False, message=f"公开Bot失败: {str(e)}", error_code=500, data=None)


@router.get("/search/by-conditions", response_model=ApiResponse)
async def search_bots_by_conditions(
    public: Optional[str] = Query(None, description="公开状态: 0=私有, 1=公开"),
    bot_name: Optional[str] = Query(None, description="Bot名称(模糊查询)"),
    owner_name: Optional[str] = Query(None, description="所有者名称"),
    bot_id: Optional[str] = Query(None, description="Bot ID(精确匹配)"),
    page: int = Query(1, ge=1, description="页码(从1开始)"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
user: AuthenticatedUser = Depends(get_current_user),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """
    根据条件分页查询 Bot。

    GET /api/bots/search/by-conditions?public=1&bot_name=xxx&owner_name=xxx&bot_id=xxx&page=1&page_size=20

    Args:
        public: 公开状态筛选 (可选)
        bot_name: Bot名称模糊查询 (可选)
        owner_name: 所有者名称筛选 (可选)
        bot_id: Bot ID精确匹配 (可选)
        page: 页码，从1开始
        page_size: 每页数量

    Returns:
        分页结果 {total, items}
    """
    # 安全措施：仅允许查询公开Bot，私有Bot需归属自己
    effective_public = public if public else "1"
    if not public:
        logger.info(f"[search_bots_by_conditions] user={user.staffId} 未指定public参数，默认仅查询公开Bot")
    try:
        result = bot_service.list_bots_by_conditions(
            public=effective_public,
            bot_name=bot_name,
            owner_name=owner_name,
            bot_id=bot_id,
            page=page,
            page_size=page_size,
        )
        # 白名单过滤：仅返回公开接口允许的字段，防止新增字段泄露
        allowed_fields = {
            "id", "bot_id", "bot_name", "bot_desc", "bot_type", "status", "public",
            "entity_id", "entity_type", "creator_id", "owner_id", "owner_name",
            "modifier_id", "share_policy", "is_delete", "env",
            "engine_types", "active_engine", "gmt_create", "gmt_modified",
        }
        original_count = len(result.get("items", []))
        filtered_items = []
        for item in result.get("items", []):
            filtered_item = {k: v for k, v in item.items() if k in allowed_fields}
            filtered_items.append(filtered_item)
        result["items"] = filtered_items
        logger.info(f"[search_bots_by_conditions] user={user.staffId} 查询完成, total={result.get('total', 0)}, 返回{original_count}条记录, 已按白名单过滤(允许{len(allowed_fields)}个字段)")
        return ApiResponse(success=True, message="查询成功", error_code=0, data=result)
    except Exception as e:
        logger.error(f"[bot_router.search_bots_by_conditions] Error: {e}")
        return ApiResponse(success=False, message=f"查询Bot失败: {str(e)}", error_code=500, data=None)
