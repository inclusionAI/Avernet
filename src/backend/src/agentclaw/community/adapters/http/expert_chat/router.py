"""
ExpertChat Router — HTTP接口入口

提供用户与专家Bot对话管理接口：
- POST /api/v1/expert-chats - 添加专家Bot到对话列表
- GET /api/v1/expert-chats - 获取对话列表
- DELETE /api/v1/expert-chats/{bot_id}/{owner_id} - 从对话列表移除
- POST /api/v1/expert-chats/{bot_id}/{owner_id}/session - 获取/创建 Session
- DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/session - 删除 Session
- GET /api/v1/expert-chats/{bot_id}/{owner_id}/sessions - 获取多会话列表
- POST /api/v1/expert-chats/{bot_id}/{owner_id}/sessions - 新建会话
- POST /api/v1/expert-chats/{bot_id}/{owner_id}/sessions/connect - 连接已有会话
- DELETE /api/v1/expert-chats/{bot_id}/{owner_id}/sessions - 删除指定会话
- POST /api/v1/expert-chats/caller-connection - 获取 caller 独立容器连接(管理员接口)

依赖规则：
  OK:  import api.expert_chat.schemas          (同层)
  OK:  import core.expert_chat.dependencies    (DI工厂)
  OK:  import core.expert_chat.errors          (业务异常)
  BAD: import plugins.*                   (never import impl directly)
"""
import time
import traceback

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.expert_chat.schemas import (
    AddChatBotRequest,
    ApiResponse,
    SessionKeyRequest,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.expert_chat_instance_service import (
    ExpertChatInstanceServiceProtocol,
)
from agentclaw.community.core.access.admin_scopes import super_admin
from agentclaw.community.di import Injected
from agentclaw.community.core.expert_chat.errors import (
    BotNotFoundError,
    BotNotActiveError,
    BotNotPublishedError,
    ChatPermissionError,
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

    except ConnectionError as e:
        # Runtime cleanup is intentionally retryable: the service preserves local
        # session ownership until every Adapter session has been deleted.
        return ApiResponse(success=False, message=str(e), error_code=int(e.error_code))
    except Exception as e:
        logger.error(f"[expert_chats.remove_chat_bot] Error: {e}")
        logger.error(f"[expert_chats.remove_chat_bot] Traceback: {traceback.format_exc()}")
        return ApiResponse(success=False, message="移除专家Bot失败，请稍后重试", error_code=5999)


@router.post("/{bot_id}/{owner_id}/session", response_model=ApiResponse)
async def get_chat_session(
    request: Request,
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
        # 从 cookie 获取 IAM_TOKEN
        iam_token = request.cookies.get("IAM_TOKEN") or None

        result = await service.get_chat_session(
            user_id=user.staffId,
            bot_id=bot_id,
            owner_id=owner_id,
            iam_token=iam_token
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
        logger.error(
            f"[expert_chats.delete_chat_session] Traceback: {traceback.format_exc()}"
        )
        return ApiResponse(
            success=False, message="删除 Session 失败，请稍后重试", error_code=5999
        )


@router.get("/{bot_id}/{owner_id}/sessions", response_model=ApiResponse)
async def list_chat_sessions(
    request: Request,
    bot_id: str,
    owner_id: str,
    session_key: str | None = Query(
        None,
        min_length=1,
        max_length=255,
        description="Exact session key filter",
    ),
    favorite_only: bool = Query(False, description="Return favorite sessions only"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
):
    """List the authenticated user's sessions for one expert Bot."""
    try:
        result = await service.list_chat_sessions(
            user_id=user.staffId,
            bot_id=bot_id,
            owner_id=owner_id,
            session_key=session_key,
            favorite_only=favorite_only,
            limit=limit,
            offset=offset,
            iam_token=request.cookies.get("IAM_TOKEN") or None,
        )
        return ApiResponse(
            success=True,
            message="获取成功",
            error_code=0,
            data=result,
        )
    except (BotNotFoundError, BotNotActiveError) as error:
        return ApiResponse(
            success=False,
            message=str(error),
            error_code=404 if isinstance(error, BotNotFoundError) else 400,
        )
    except BotNotPublishedError as error:
        return ApiResponse(
            success=False, message=str(error), error_code=4001
        )
    except ChatPermissionError as error:
        return ApiResponse(
            success=False, message=str(error), error_code=403
        )
    except ConnectionError as error:
        return ApiResponse(
            success=False,
            message=str(error),
            error_code=int(error.error_code) if error.error_code else 5001,
        )
    except Exception as error:
        logger.error(
            "[expert_chats.list_chat_sessions] Unexpected error: %s: %s",
            type(error).__name__,
            error,
        )
        logger.error(traceback.format_exc())
        return ApiResponse(
            success=False,
            message="获取会话列表失败，请稍后重试",
            error_code=5999,
        )


@router.post("/{bot_id}/{owner_id}/sessions", response_model=ApiResponse)
async def create_chat_session(
    request: Request,
    bot_id: str,
    owner_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
):
    """Create a new session without replacing older session mappings."""
    try:
        result = await service.create_chat_session(
            user_id=user.staffId,
            bot_id=bot_id,
            owner_id=owner_id,
            iam_token=request.cookies.get("IAM_TOKEN") or None,
        )
        return ApiResponse(
            success=True,
            message="创建成功",
            error_code=0,
            data=result,
        )
    except (BotNotFoundError, BotNotActiveError) as error:
        return ApiResponse(
            success=False,
            message=str(error),
            error_code=404 if isinstance(error, BotNotFoundError) else 400,
        )
    except BotNotPublishedError as error:
        return ApiResponse(
            success=False, message=str(error), error_code=4001
        )
    except ChatPermissionError as error:
        return ApiResponse(
            success=False, message=str(error), error_code=403
        )
    except (ConnectionError, SessionCreateError) as error:
        return ApiResponse(
            success=False,
            message=str(error),
            error_code=int(error.error_code) if error.error_code else 5003,
        )
    except Exception as error:
        logger.error(
            "[expert_chats.create_chat_session] Unexpected error: %s: %s",
            type(error).__name__,
            error,
        )
        logger.error(traceback.format_exc())
        return ApiResponse(
            success=False,
            message="创建 Session 失败，请稍后重试",
            error_code=5999,
        )


@router.post("/{bot_id}/{owner_id}/sessions/connect", response_model=ApiResponse)
async def connect_chat_session(
    request: Request,
    bot_id: str,
    owner_id: str,
    body: SessionKeyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
):
    """Return connection data for one session owned by the caller."""
    try:
        result = await service.connect_chat_session(
            user_id=user.staffId,
            bot_id=bot_id,
            owner_id=owner_id,
            session_key=body.session_key,
            iam_token=request.cookies.get("IAM_TOKEN") or None,
        )
        return ApiResponse(
            success=True,
            message="获取成功",
            error_code=0,
            data=result,
        )
    except (BotNotFoundError, BotNotActiveError) as error:
        return ApiResponse(
            success=False,
            message=str(error),
            error_code=404 if isinstance(error, BotNotFoundError) else 400,
        )
    except BotNotPublishedError as error:
        return ApiResponse(
            success=False, message=str(error), error_code=4001
        )
    except ChatPermissionError as error:
        return ApiResponse(
            success=False, message=str(error), error_code=403
        )
    except ConnectionError as error:
        return ApiResponse(
            success=False,
            message=str(error),
            error_code=int(error.error_code) if error.error_code else 5001,
        )
    except Exception as error:
        logger.error(
            "[expert_chats.connect_chat_session] Unexpected error: %s: %s",
            type(error).__name__,
            error,
        )
        logger.error(traceback.format_exc())
        return ApiResponse(
            success=False,
            message="连接 Session 失败，请稍后重试",
            error_code=5999,
        )


@router.delete("/{bot_id}/{owner_id}/sessions", response_model=ApiResponse)
async def delete_owned_chat_session(
    bot_id: str,
    owner_id: str,
    session_key: str = Query(..., min_length=1, max_length=255),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ExpertChatServiceProtocol = Injected(ExpertChatServiceProtocol),
):
    """Delete one session after checking its Backend-owned mapping."""
    try:
        await service.delete_owned_chat_session(
            user_id=user.staffId,
            bot_id=bot_id,
            owner_id=owner_id,
            session_key=session_key,
        )
        return ApiResponse(
            success=True,
            message="Session 已删除",
            error_code=0,
        )
    except BotNotFoundError as error:
        return ApiResponse(
            success=False,
            message=str(error),
            error_code=404,
        )
    except BotNotActiveError as error:
        return ApiResponse(
            success=False, message=str(error), error_code=400
        )
    except ChatPermissionError as error:
        return ApiResponse(
            success=False, message=str(error), error_code=403
        )
    except ConnectionError as error:
        return ApiResponse(
            success=False,
            message=str(error),
            error_code=int(error.error_code) if error.error_code else 5001,
        )
    except Exception as error:
        logger.error(
            "[expert_chats.delete_owned_chat_session] Unexpected error: %s: %s",
            type(error).__name__,
            error,
        )
        logger.error(traceback.format_exc())
        return ApiResponse(
            success=False,
            message="删除 Session 失败，请稍后重试",
            error_code=5999,
        )


@router.post("/caller-connection", response_model=ApiResponse)
async def get_caller_connection_for_other(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot 所有者ID"),
    user_id: str = Query(..., description="调用者(Caller)用户ID"),
    force_upgrade: bool = Query(False, description="强制升级，跳过版本检查"),
    user: AuthenticatedUser = Depends(get_current_user),
    instance_service: ExpertChatInstanceServiceProtocol = Injected(
        ExpertChatInstanceServiceProtocol
    ),
):
    """Get or restart an authorized caller's existing container instance."""
    started_at = time.perf_counter()
    operator_id = user.staffId
    log_args = (operator_id, bot_id, owner_id, user_id, force_upgrade)
    logger.info(
        "event=expert_chat.caller_connection.request direction=inbound "
        "operation=caller_connection method=POST "
        "route=/api/v1/expert-chats/caller-connection operator_id=%s "
        "bot_id=%s owner_id=%s user_id=%s force_upgrade=%s",
        *log_args,
    )

    if not operator_id or operator_id == "anonymous":
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.warning(
            "event=expert_chat.caller_connection.denied direction=inbound "
            "operation=caller_connection method=POST "
            "route=/api/v1/expert-chats/caller-connection operator_id=%s "
            "bot_id=%s owner_id=%s user_id=%s force_upgrade=%s "
            "reason=missing_operator duration_ms=%.1f",
            *log_args,
            duration_ms,
        )
        return ApiResponse(
            success=False,
            message="无法获取操作者信息",
            error_code=400,
            data=None,
        )

    try:
        is_super_admin = operator_id in super_admin()
        result = await instance_service.get_authorized_caller_connection(
            operator_id=operator_id,
            user_id=user_id,
            bot_id=bot_id,
            owner_id=owner_id,
            is_super_admin=is_super_admin,
            force_upgrade=force_upgrade,
        )
        duration_ms = (time.perf_counter() - started_at) * 1000
        authorized_as = "admin" if is_super_admin else "self"
        logger.info(
            "event=expert_chat.caller_connection.success direction=inbound "
            "operation=caller_connection method=POST "
            "route=/api/v1/expert-chats/caller-connection operator_id=%s "
            "bot_id=%s owner_id=%s user_id=%s force_upgrade=%s "
            "authorized_as=%s need_poll=%s duration_ms=%.1f",
            *log_args,
            authorized_as,
            result.get("need_poll"),
            duration_ms,
        )
        return ApiResponse(
            success=True, message="获取成功", error_code=0, data=result
        )
    except ChatPermissionError as error:
        duration_ms = (time.perf_counter() - started_at) * 1000
        reason = getattr(error, "reason", "permission_denied")
        logger.warning(
            "event=expert_chat.caller_connection.denied direction=inbound "
            "operation=caller_connection method=POST "
            "route=/api/v1/expert-chats/caller-connection operator_id=%s "
            "bot_id=%s owner_id=%s user_id=%s force_upgrade=%s "
            "reason=%s duration_ms=%.1f",
            *log_args,
            reason,
            duration_ms,
        )
        return ApiResponse(
            success=False,
            message="无权限执行此操作",
            error_code=403,
            data=None,
        )
    except Exception as error:
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.error(
            "event=expert_chat.caller_connection.failed direction=inbound "
            "operation=caller_connection method=POST "
            "route=/api/v1/expert-chats/caller-connection operator_id=%s "
            "bot_id=%s owner_id=%s user_id=%s force_upgrade=%s "
            "exception_type=%s duration_ms=%.1f",
            *log_args,
            type(error).__name__,
            duration_ms,
        )
        return ApiResponse(
            success=False,
            message="获取 Caller 连接失败，请稍后重试",
            error_code=5999,
        )
