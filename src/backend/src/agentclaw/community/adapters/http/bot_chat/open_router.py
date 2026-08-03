"""Read-only bot-chat endpoints for exact embed lookups."""

from fastapi import APIRouter, Path, Query

from agentclaw.community.api.bot_chat_service import BotChatServiceProtocol
from agentclaw.community.core.bot_chat.errors import SessionNotFoundError
from agentclaw.community.core.bot_chat.schemas import (
    ApiResponse,
    ConversationDetail,
    SessionListResponse,
)
from agentclaw.community.di import Injected

router = APIRouter(prefix="/api/v1/open/bot-chats", tags=["bot-chats-open"])


@router.get("", response_model=ApiResponse[SessionListResponse])
async def list_open_sessions(
    session_key: str | None = Query(default=None, max_length=512),
    biz_scene: str | None = Query(default=None, max_length=128),
    biz_task_id: str | None = Query(default=None, max_length=256),
    group_id: str | None = Query(default=None, max_length=256),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
    service: BotChatServiceProtocol = Injected(BotChatServiceProtocol),
):
    """List traces for one exact Session, Task, or BCS group identifier."""
    try:
        result = await service.list_open_sessions(
            session_key=session_key,
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            group_id=group_id,
            page=page,
            limit=limit,
        )
        return ApiResponse(success=True, message="ok", data=result)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=4000)
    except Exception as exc:
        return ApiResponse(success=False, message=str(exc), error_code=5999)


@router.get(
    "/users/{user_id}/bots/{bot_id}/traces",
    response_model=ApiResponse[SessionListResponse],
)
async def list_open_user_bot_traces(
    user_id: str = Path(max_length=256),
    bot_id: str = Path(max_length=256),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
    service: BotChatServiceProtocol = Injected(BotChatServiceProtocol),
):
    """List recent traces and their Session, Task, and Group labels."""
    try:
        result = await service.list_open_user_bot_traces(
            user_id=user_id,
            bot_id=bot_id,
            page=page,
            limit=limit,
        )
        return ApiResponse(success=True, message="ok", data=result)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=4000)
    except Exception as exc:
        return ApiResponse(success=False, message=str(exc), error_code=5999)


@router.get("/{trace_id}", response_model=ApiResponse[ConversationDetail])
async def get_open_session(
    trace_id: str,
    service: BotChatServiceProtocol = Injected(BotChatServiceProtocol),
):
    """Get an exact trace and observation tree without owner filtering."""
    try:
        result = await service.get_open_session(trace_id)
        return ApiResponse(success=True, message="ok", data=result)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=4000)
    except SessionNotFoundError as exc:
        return ApiResponse(success=False, message=str(exc), error_code=4004)
    except Exception as exc:
        return ApiResponse(success=False, message=str(exc), error_code=5999)
