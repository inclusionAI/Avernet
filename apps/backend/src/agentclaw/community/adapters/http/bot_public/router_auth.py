"""Bot public auth API routes.

提供 Bot 公开好友关系相关的接口：
- POST /friend-request-approval - 创建公开Bot好友申请审批单
- POST /approval_callback - 审批回调接口
- GET /search - 根据关键词分页查询公开 Bot
- GET /my-friend-bots - 查询当前用户的 Bot 好友列表
- GET /friend-record - 查询当前用户与目标 Bot 的好友关系记录
"""
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.bot_public.schemas import ApiResponse
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.bot_public.services.bot_public_service import (
    BotNotPublicError,
    BotPublicServiceError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


logger = get_logger()

router = APIRouter(prefix="/api/v1/bot-public", tags=["bot-public"])


# ==================== APIs ====================

@router.post("/friend-request-approval", response_model=ApiResponse[dict[str, Any]])
async def create_friend_request_approval(
    bot_id: str,
    owner_id: str,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> ApiResponse[dict[str, Any]]:
    """创建公开Bot好友申请审批单。

    Args:
        bot_id: Bot ID
        owner_id: Owner ID
        user: 当前登录用户

    Returns:
        审批结果，包含 success, puid, approval_url, error_msg
    """
    try:
        result = service.create_friend_request_approval(
            bot_id=bot_id,
            owner_id=owner_id,
            operator_id=user.staffId,
            operator_name=user.nickName or user.staffId,
        )
        return ApiResponse(success=result.get("success", False), message="OK", error_code=200, data=result)
    except BotNotPublicError as e:
        return ApiResponse(success=False, message=str(e), error_code=400, data={})
    except BotPublicServiceError as e:
        return ApiResponse(success=False, message=str(e), error_code=500, data={})


@router.post("/approval_callback", summary="审批回调接口")
async def approval_callback(
    request: Request,
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
):
    """接收流程平台审批结果回调。

    支持表单格式 (application/x-www-form-urlencoded)

    回调数据包含：
    - globalUniqueId: puid
    - lastOperate: AGREE/DISAGREE/CANCEL
    - context 中的数据：id, bot_public_owner_audit
    """
    form_data = await request.form()
    data = dict(form_data)

    logger.info("[bot_public_callback] 收到表单数据: %s", data)

    global_unique_id = data.get("globalUniqueId") or ""
    last_operate = (data.get("lastOperate") or "").upper()
    bot_friend_id = int(data.get("bot_friend_id") or 0)

    return service.handle_friend_request_approval_callback(
        puid=global_unique_id,
        last_operate=last_operate,
        bot_friend_id=bot_friend_id,
    )


@router.get("/search", response_model=ApiResponse[dict[str, Any]])
async def search_public_bots_by_keyword(
    search: str | None = Query(None, description="搜索关键词(owner_name或bot_name模糊查询)"),
    page: int = Query(1, ge=1, description="页码(从1开始)"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> ApiResponse[dict[str, Any]]:
    """根据关键词分页查询公开 Bot。

    GET /api/v1/bot-public/search?search=xxx&page=1&page_size=20

    Args:
        search: 搜索关键词，模糊匹配 owner_name 或 bot_name (可选)
        page: 页码，从1开始
        page_size: 每页数量
        user: 当前登录用户

    Returns:
        分页结果 {total, items}
    """
    try:
        result = service.search_public_bots_by_keyword(
            user_id=user.staffId,
            search=search,
            page=page,
            page_size=page_size,
        )

        return ApiResponse(success=True, message="OK", error_code=200, data=result)
    except Exception as e:
        logger.error(f"[bot_public.search_public_bots_by_keyword] Error: {e}")
        return ApiResponse(success=False, message=f"查询Bot失败: {str(e)}", error_code=500, data={})


@router.get("/my-friend-bots", response_model=ApiResponse[dict[str, Any]])
async def search_my_friend_bots(
    search: str | None = Query(None, description="搜索关键词(owner_name或bot_name模糊查询)"),
    page: int = Query(1, ge=1, description="页码(从1开始)"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> ApiResponse[dict[str, Any]]:
    """查询当前用户的 Bot 好友列表。

    GET /api/v1/bot-public/my-friend-bots?search=xxx&page=1&page_size=20

    Args:
        search: 搜索关键词，模糊匹配 owner_name 或 bot_name (可选)
        page: 页码，从1开始
        page_size: 每页数量
        user: 当前登录用户

    Returns:
        分页结果 {total, items}
    """
    try:
        result = service.list_my_bot_friends(
            user_id=user.staffId,
            search=search,
            page=page,
            page_size=page_size,
        )

        return ApiResponse(success=True, message="OK", error_code=200, data=result)
    except Exception as e:
        logger.error(f"[bot_public.search_my_friend_bots] Error: {e}")
        return ApiResponse(success=False, message=f"查询好友Bot失败: {str(e)}", error_code=500, data={})


@router.get("/friend-record", response_model=ApiResponse[dict[str, Any]])
async def get_friend_record(
    target_entity_id: str = Query(..., description="目标方实体ID"),
    target_bot_id: str = Query(..., description="目标Bot ID"),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
    service: BotPublicServiceProtocol = Injected(BotPublicServiceProtocol),
) -> ApiResponse[dict[str, Any]]:
    """查询当前用户与目标 Bot 的好友关系记录。

    GET /api/v1/bot-public/friend-record?target_entity_id=xxx&target_bot_id=xxx

    Args:
        target_entity_id: 目标方实体ID
        target_bot_id: 目标Bot ID
        user: 当前登录用户

    Returns:
        好友记录信息，不存在则返回 null
    """
    try:
        result = service.get_friend_record(
            user_id=user.staffId,
            target_entity_id=target_entity_id,
            target_bot_id=target_bot_id,
        )

        return ApiResponse(success=True, message="OK", error_code=200, data=result)
    except Exception as e:
        logger.error(f"[bot_public.get_friend_record] Error: {e}")
        return ApiResponse(success=False, message=f"查询好友记录失败: {str(e)}", error_code=500, data={})


@router.get("/discover", response_model=ApiResponse[dict[str, Any]])
async def discover(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    top_k: int = Query(10, ge=1, le=20, description="返回结果数量（1-20）"),
    min_score: float = Query(0.1, ge=0.0, le=1.0, description="最小推荐分数（0-1）"),
    filters: str | None = Query('{"runtime_state": ["online"]}', description='过滤条件 JSON 字符串，默认 {"runtime_state": ["online"]}'),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
    discover_service: BotDiscoverServiceProtocol = Injected(BotDiscoverServiceProtocol),
) -> ApiResponse[dict[str, Any]]:
    """
    根据关键词发现公开的 Bot.

    该接口封装了对 BCSFuse recommend 接口的调用，并过滤掉非 public 的 bot。

    GET /api/v1/bot-public/discover?keyword=安全&top_k=5&min_score=0.01

    Args:
        keyword: 搜索关键词（必需）
        top_k: 返回结果数量（默认 10，最大 20）
        min_score: 最小推荐分数（默认 0.01，范围 0-1）
        filters: 过滤条件 JSON 字符串，默认 {"runtime_state": ["online"]}
        user: 当前登录用户

    Returns:
        搜索结果字典（与 search 接口格式一致）:
        {
            "total": 1,
            "items": [
                {
                    "id": 209110,
                    "bot_id": "default",
                    "bot_name": "xxx",
                    "owner_id": "100000",
                    "owner_name": "xxx",
                    "public": "1",
                    ...,
                    "recommend": {
                        "profile_key": "bot_xxx:default",
                        "score": 0.85,
                        "reasons": [...]
                    }
                }
            ]
        }
    """
    try:
        import json

        logger.info(f"[bot_public.discover] 搜索请求: keyword={keyword}, top_k={top_k}")

        # 解析 filters 参数
        filters_dict: dict | None = None
        if filters:
            try:
                filters_dict = json.loads(filters)
            except json.JSONDecodeError:
                return ApiResponse(
                    success=False,
                    message="filters 参数格式错误: 无效的 JSON",
                    error_code=400,
                    data=None,
                )

        result = discover_service.search_by_keyword(
            keyword=keyword,
            user_id=user.staffId,
            top_k=top_k,
            min_score=min_score,
            filters=filters_dict,
        )

        return ApiResponse(
            success=True,
            message="搜索成功",
            error_code=200,
            data=result,
        )

    except Exception as e:
        logger.error(f"[bot_public.discover] 搜索失败: {e}")
        return ApiResponse(
            success=False,
            message=f"搜索Bot失败: {str(e)}",
            error_code=500,
            data=None,
        )
