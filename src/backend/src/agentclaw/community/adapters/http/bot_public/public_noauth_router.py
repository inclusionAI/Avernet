"""Bot Public No-Auth Router.

提供免鉴权版本的 Bot 相关接口：
- GET /api/public/bots/{bot_id}/appcoding-bots - 获取架构师 bot 关联的 coding bots（免鉴权）
- PATCH /api/public/bots/{bot_id}/ext - 更新 bot ext 字段（免鉴权，限制字段）

注意：
- 这些接口不检查用户身份，任何人均可访问
- /ext 接口限制只能更新特定白名单字段，敏感操作仍需鉴权版本
"""
from typing import Any, Optional

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotServiceError,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/public/bots", tags=["bot-public-noauth"])


# ==================== Response Models ====================


class ApiResponse(BaseModel):
    """Unified API response format."""
    success: bool
    message: str = "OK"
    error_code: int = 200
    data: Optional[Any] = None


# ==================== Ext Update Configuration ====================

# 免鉴权 /ext 接口允许更新的字段白名单
# 只允许更新非敏感的、公開的配置字段
EXT_UPDATE_WHITELIST = {
    # 架构域标识
    "arch_domain",
    "is_domain_bot",
}


def _filter_ext_update(ext_update: dict[str, Any]) -> dict[str, Any]:
    """过滤 ext 更新字段，只允许更新白名单中的字段。

    Args:
        ext_update: 原始更新数据

    Returns:
        过滤后的数据（只包含白名单字段）
    """
    return {k: v for k, v in ext_update.items() if k in EXT_UPDATE_WHITELIST}


# ==================== Public Endpoints (No Auth Required) ====================


@router.get("/{bot_id}/appcoding-bots", response_model=ApiResponse)
async def list_coding_bots_by_architect_public(
    bot_id: str = Path(..., description="架构师 Bot ID"),
    _bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> ApiResponse:
    """免鉴权获取架构师 bot 关联的 coding bots。

    GET /api/public/bots/{bot_id}/appcoding-bots

    与鉴权版本 /api/bots/{bot_id}/appcoding-bots 功能相同，但不需要任何身份验证。
    返回完整的 bot 字段。

    Args:
        bot_id: 架构师 Bot ID (domain architect bot)

    Returns:
        关联的应用 coding bots 列表（完整字段）
    """
    try:
        coding_bots = _bot_service.list_coding_bots_by_architect(bot_id)

        logger.info(
            f"[public_noauth.list_coding_bots] bot_id={bot_id}, "
            f"total={len(coding_bots)}"
        )

        return ApiResponse(
            success=True,
            data=coding_bots,
        )
    except BotServiceError as e:
        logger.error(f"[public_noauth.list_coding_bots] Service error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取Coding Bot列表失败: {str(e)}",
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[public_noauth.list_coding_bots] Unexpected error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取Coding Bot列表失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.patch("/{bot_id}/ext", response_model=ApiResponse)
async def update_bot_ext_public(
    bot_id: str = Path(..., description="Bot ID"),
    request: Request = None,
    bot_repo: BotRepository = Injected(BotRepository),
) -> ApiResponse:
    """免鉴权局部更新 bot ext 字段（限制白名单）。

    PATCH /api/public/bots/{bot_id}/ext
    Body: { "key1": "value1", ... }  # 只允许白名单字段

    与鉴权版本 /api/bots/{bot_id}/ext 的区别：
    1. 不需要任何身份验证
    2. 只允许更新白名单中的非敏感字段
    3. 敏感字段（如 authorized_sources, iam_token 等）会被自动过滤

    允许更新的字段：
    - ui_state, view_mode, collapsed_panels（前端状态）
    - client_session_id, last_active_at（临时标记）

    Args:
        bot_id: Bot ID
        request: FastAPI request object containing JSON body with fields to update

    Returns:
        更新结果
    """
    try:
        ext_update = await request.json()
        if not isinstance(ext_update, dict):
            return ApiResponse(
                success=False,
                message="请求体必须是 JSON 对象",
                error_code=400,
                data=None,
            )

        # 过滤白名单字段
        filtered_update = _filter_ext_update(ext_update)

        if not filtered_update:
            logger.warning(
                f"[public_noauth.update_bot_ext] bot_id={bot_id} "
                f"no valid fields to update (original: {list(ext_update.keys())})"
            )
            return ApiResponse(
                success=False,
                message="没有有效的字段可更新，只允许更新白名单字段",
                error_code=400,
                data={
                    "allowed_fields": list(EXT_UPDATE_WHITELIST),
                    "received_fields": list(ext_update.keys()),
                },
            )

        # 直接通过 repository 获取 bot（免鉴权，不检查 owner）
        total, items = bot_repo.list_by_conditions(bot_id=bot_id, page=1, page_size=1)
        if not items:
            return ApiResponse(
                success=False,
                message=f"Bot不存在: {bot_id}",
                error_code=404,
                data=None,
            )

        bot = items[0]
        owner_id = bot.get("owner_id")

        if not owner_id:
            return ApiResponse(
                success=False,
                message="Bot 没有关联的 owner_id",
                error_code=500,
                data=None,
            )

        # 获取当前 ext 并合并更新
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                import json
                ext = json.loads(ext)
            except json.JSONDecodeError:
                ext = {}

        # 只更新白名单字段
        ext.update(filtered_update)

        # 通过 repository 直接更新（免鉴权版本不检查 owner）
        bot_repo.update_by_owner(bot_id, owner_id, {"ext": ext})

        logger.info(
            f"[public_noauth.update_bot_ext] Updated bot_id={bot_id} "
            f"fields={list(filtered_update.keys())}"
        )

        return ApiResponse(
            success=True,
            message="ext 更新成功",
            data={
                "updated_fields": list(filtered_update.keys()),
                "bot_id": bot_id,
            },
        )

    except Exception as e:
        logger.error(f"[public_noauth.update_bot_ext] bot_id={bot_id} error: {e}")
        return ApiResponse(
            success=False,
            message=f"更新Bot ext失败: {str(e)}",
            error_code=500,
            data=None,
        )
