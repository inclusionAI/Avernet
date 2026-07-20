"""AICoding one-click architect-bot rebind route.

``PUT /api/bots/{architect_bot_id}/architect-rebind`` is an aicoding-creation
specific endpoint (it binds ``applicationCoding`` bots to a domain architect
bot), so it lives under the aicoding HTTP package instead of the generic
bot-management router. The path is kept under ``/api/bots`` because it
operates on bot resources; only the owning code moves.
"""
from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.api.aicoding.architect_rebind_service import ArchitectRebindServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotPermissionError,
    BotServiceError,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/bots", tags=["aicoding"])


class ApiResponse(BaseModel):
    """Unified API response envelope for the rebind route."""

    success: bool
    message: str = "OK"
    error_code: int = 200
    data: object = None


class RebindArchitectRequest(BaseModel):
    """请求模型：一键换绑架构师 bot（把一批应用 coding bot 绑定到指定架构师 bot）。"""

    coding_bot_ids: List[str]

    @field_validator("coding_bot_ids")
    @classmethod
    def _coding_bot_ids_non_empty(cls, value: List[str]) -> List[str]:
        cleaned = [x.strip() for x in value if isinstance(x, str) and x.strip()]
        if not cleaned:
            raise ValueError("coding_bot_ids 不能为空")
        return cleaned


@router.put("/{architect_bot_id}/architect-rebind", response_model=ApiResponse)
async def rebind_architect_bot(
    architect_bot_id: str,
    payload: RebindArchitectRequest,
    ctx: RequestContext = Depends(get_request_context),
    _architect_rebind_service: ArchitectRebindServiceProtocol = Injected(ArchitectRebindServiceProtocol),
) -> ApiResponse:
    """一键换绑架构师 bot（支持批量）：把应用 coding bot 绑定到指定架构师 bot。

    PUT /api/bots/{architect_bot_id}/architect-rebind
    Body: {"coding_bot_ids": ["app_coding_bot_id_1", ...]}

    - {architect_bot_id} 须为 domain architect bot (ext.is_domain_bot == true)
    - coding_bot_ids 为 applicationCoding bot 列表，自动去重保序
    - 仅允许架构师 bot 的 owner 调用，非 owner -> 403
    - 落库 ac_templates.ext.architect_bot_id；单条失败不影响其余
    """
    try:
        operator_id = ctx.user_id
        if not operator_id or operator_id == "anonymous":
            return ApiResponse(
                success=False,
                message="无法获取用户信息",
                error_code=400,
                data=None,
            )
        result = await asyncio.to_thread(
            _architect_rebind_service.rebind_architect_bot_batch,
            coding_bot_ids=payload.coding_bot_ids,
            architect_bot_id=architect_bot_id,
            operator_id=operator_id,
        )
        return ApiResponse(success=True, data=result)
    except BotNotFoundError:
        return ApiResponse(
            success=False,
            message="架构师 Bot 不存在",
            error_code=404,
            data=None,
        )
    except BotPermissionError as e:
        logger.warning("[aicoding.rebind_architect_bot] permission error: %s", e)
        return ApiResponse(success=False, message=str(e), error_code=403, data=None)
    except BotServiceError as e:
        logger.warning("[aicoding.rebind_architect_bot] service error: %s", e)
        return ApiResponse(success=False, message=str(e), error_code=400, data=None)
    except Exception as e:
        logger.error(f"[aicoding.rebind_architect_bot] Error: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"换绑架构师 Bot 失败: {str(e)}",
            error_code=500,
            data=None,
        )
