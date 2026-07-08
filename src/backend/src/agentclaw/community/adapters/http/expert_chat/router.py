"""
ExpertChat Router — HTTP接口入口

提供用户与专家Bot对话管理接口：
- POST /api/v1/expert-chats - 添加专家Bot到对话列表
- GET /api/v1/expert-chats - 获取对话列表
- DELETE /api/v1/expert-chats/{bot_id}/{owner_id} - 从对话列表移除
- POST /api/v1/expert-chats/{bot_id}/{owner_id}/session - 获取/创建 Session
- DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/session - 删除 Session

依赖规则：
  OK:  import api.expert_chat.schemas          (同层)
  OK:  import core.expert_chat.dependencies    (DI工厂)
  OK:  import core.expert_chat.errors          (业务异常)
  BAD: import plugins.*                   (never import impl directly)
"""
import traceback

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.expert_chat.schemas import (
    AddChatBotRequest,
    ApiResponse,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError,
    BotNotActiveError,
    BotNotPublishedError,
    SessionCreateError,
    ConnectionError,
)
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/v1/expert-chats", tags=["expert-chats"])


# ============ API Endpoints ============

@router.post("", response_model=ApiResponse)
async def add_chat_bot(
    request: AddChatBotRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol)
):
    """
    添加专家Bot到用户对话列表

    需要传 bot_id 和 owner_id，后端通过 get_by_id_and_owner 唯一定位 Bot
    """
    try:
        result = service.add_chat_bot(
            user_id=user.staffId,
            bot_id=request.bot_id,
            owner_id=request.owner_id
        )
        return ApiResponse(success=True, message="添加成功", error_code=0, data=result)

    except BotNotFoundError as e:
        logger.warning(f"[expert_chats.add_chat_bot] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404)

    except BotNotActiveError as e:
        logger.warning(f"[expert_chats.add_chat_bot] Bot not active: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400)

    except BotNotPublishedError as e:
        logger.warning(f"[expert_chats.add_chat_bot] Bot not published: {e}")
        return ApiResponse(success=False, message=str(e), error_code=4001)

    except Exception as e:
        logger.error(f"[expert_chats.add_chat_bot] Error: {e}")
        logger.error(f"[expert_chats.add_chat_bot] Traceback: {traceback.format_exc()}")
        return ApiResponse(success=False, message="添加专家Bot失败，请稍后重试", error_code=5999)


@router.get("", response_model=ApiResponse)
async def list_chat_bots(
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol)
):
    """
    获取用户对话列表中的专家Bot

    实时查询 ac_bots 表获取 bot_name 和 owner_name
    """
    try:
        items = service.list_chat_bots(user_id=user.staffId)
        return ApiResponse(success=True, message="获取成功", error_code=0, data={
            "total": len(items),
            "items": items
        })

    except Exception as e:
        logger.error(f"[expert_chats.list_chat_bots] Error: {e}")
        logger.error(f"[expert_chats.list_chat_bots] Traceback: {traceback.format_exc()}")
        return ApiResponse(success=False, message="获取专家Bot列表失败，请稍后重试", error_code=5999)


@router.delete("/{bot_id}/{owner_id}", response_model=ApiResponse)
async def remove_chat_bot(
    bot_id: str,
    owner_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol)
):
    """
    从对话列表移除专家Bot（软删除）

    需要传 bot_id 和 owner_id 唯一定位要移除的 Bot
    """
    try:
        success = await service.remove_chat_bot(
            user_id=user.staffId,
            bot_id=bot_id,
            owner_id=owner_id
        )
        if success:
            return ApiResponse(success=True, message="专家Bot已移除", error_code=0)
        else:
            return ApiResponse(success=False, message="专家Bot不存在或不在对话列表中", error_code=404)

    except Exception as e:
        logger.error(f"[expert_chats.remove_chat_bot] Error: {e}")
        logger.error(f"[expert_chats.remove_chat_bot] Traceback: {traceback.format_exc()}")
        return ApiResponse(success=False, message="移除专家Bot失败，请稍后重试", error_code=5999)


@router.post("/{bot_id}/{owner_id}/session", response_model=ApiResponse)
async def get_chat_session(
    bot_id: str,
    owner_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol)
):
    """
    获取/创建与专家Bot的 chat session

    通过 bot_id + owner_id 唯一定位 Bot，返回 session_key 和 connection 信息
    """
    try:
        result = await service.get_chat_session(
            user_id=user.staffId,
            bot_id=bot_id,
            owner_id=owner_id
        )
        return ApiResponse(success=True, message="获取成功", error_code=0, data=result)

    except BotNotFoundError as e:
        logger.warning(f"[expert_chats.get_chat_session] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404)

    except BotNotActiveError as e:
        logger.warning(f"[expert_chats.get_chat_session] Bot not active: {e}")
        return ApiResponse(success=False, message=str(e), error_code=400)

    except BotNotPublishedError as e:
        logger.warning(f"[expert_chats.get_chat_session] Bot not published: {e}")
        return ApiResponse(success=False, message=str(e), error_code=4001)

    except ConnectionError as e:
        logger.error(f"[expert_chats.get_chat_session] Connection error: {e.original_error}")
        error_code = int(e.error_code) if e.error_code else 5001
        return ApiResponse(success=False, message=str(e), error_code=error_code)

    except SessionCreateError as e:
        logger.error(f"[expert_chats.get_chat_session] Session create error: {e.original_error}")
        error_code = int(e.error_code) if e.error_code else 5003
        return ApiResponse(success=False, message=str(e), error_code=error_code)

    except Exception as e:
        logger.error(f"[expert_chats.get_chat_session] Unexpected error: {type(e).__name__}: {e}")
        logger.error(f"[expert_chats.get_chat_session] Traceback: {traceback.format_exc()}")
        return ApiResponse(success=False, message="获取 Session 失败，请稍后重试", error_code=5999)


@router.delete("/{bot_id}/{owner_id}/session", response_model=ApiResponse)
async def delete_chat_session(
    bot_id: str,
    owner_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol)
):
    """
    删除与专家Bot的 chat session

    通过 bot_id + owner_id 唯一定位 Bot，同时调用 Adapter 删除 session，并删除本地 session 映射
    """
    try:
        await service.delete_chat_session(
            user_id=user.staffId,
            bot_id=bot_id,
            owner_id=owner_id
        )
        return ApiResponse(success=True, message="Session 已删除", error_code=0)

    except BotNotFoundError as e:
        logger.warning(f"[expert_chats.delete_chat_session] Bot not found: {e}")
        return ApiResponse(success=False, message=str(e), error_code=404)

    except Exception as e:
        logger.error(f"[expert_chats.delete_chat_session] Error: {e}")
        logger.error(f"[expert_chats.delete_chat_session] Traceback: {traceback.format_exc()}")
        return ApiResponse(success=False, message="删除 Session 失败，请稍后重试", error_code=5999)
