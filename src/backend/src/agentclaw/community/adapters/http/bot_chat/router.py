from datetime import datetime

from fastapi import APIRouter, Depends, Query

from agentclaw.community.api.bot_chat_service import BotChatServiceProtocol
from agentclaw.community.core.bot_chat.errors import LangfuseAPIError, SessionNotFoundError
from agentclaw.community.core.bot_chat.schemas import (
    ApiResponse,
    ConversationDetail,
    HealthCheckData,
    SessionListResponse,
)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.di import Injected

router = APIRouter(prefix="/api/v1/bot-chats", tags=["bot-chats"])


@router.get("", response_model=ApiResponse[SessionListResponse])
async def list_sessions(
    owner_id: str | None = Query(default=None, description="Owner ID (defaults to current user)"),
    bot_id: str | None = Query(default=None, description="Filter by bot_id"),
    trace_id: str | None = Query(default=None, description="Filter by trace ID"),
    session_id: str | None = Query(
        default=None, description="Filter by metadata.attributes['gen_ai.session.id']"
    ),
    session_key: str | None = Query(
        default=None,
        description=(
            "Filter by OpenClaw session key in metadata.attributes['session_id'] "
            "or metadata.attributes['gen_ai.conversation.id']"
        ),
    ),
    query: str | None = Query(default=None, description="Fuzzy search on session name and user input"),
    biz_scene: str | None = Query(default=None, description="Business scene"),
    biz_task_id: str | None = Query(default=None, description="Business task ID"),
    group_id: str | None = Query(default=None, description="BCS group ID"),
    match_mode: str = Query(default="exact", pattern="^(exact|contains)$"),
    include_output_match: bool = Query(
        default=False, description="Include trace output in keyword matching"
    ),
    time_scope: str = Query(
        default="default",
        pattern="^(default|all)$",
        description="Use 'all' only for an exact identifier lookup",
    ),
    from_date: datetime | None = Query(default=None, description="Start time (ISO 8601)"),
    to_date: datetime | None = Query(default=None, description="End time (ISO 8601)"),
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    log_source: str | None = Query(
        default=None,
        description="Data source: 'db' (default) or 'langfuse'",
    ),
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotChatServiceProtocol = Injected(BotChatServiceProtocol),
):
    """List bot conversation sessions."""
    try:
        # COSEC: Bind the owner scope to the authenticated identity; never trust query owner_id.
        effective_owner_id = user.staffId
        result = await service.list_sessions(
            owner_id=effective_owner_id,
            from_date=from_date,
            to_date=to_date,
            page=page,
            limit=limit,
            bot_id=bot_id,
            trace_id=trace_id,
            session_id=session_id,
            session_key=session_key,
            query=query,
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            group_id=group_id,
            match_mode=match_mode,
            include_output_match=include_output_match,
            time_scope=time_scope,
            log_source=log_source,
        )
        return ApiResponse(success=True, message="ok", data=result)
    except ValueError as e:
        return ApiResponse(success=False, message=str(e), error_code=4000)
    except LangfuseAPIError as e:
        return ApiResponse(success=False, message=str(e), error_code=5999)
    except Exception as e:
        return ApiResponse(success=False, message=str(e), error_code=5999)


@router.get("/health", response_model=ApiResponse[HealthCheckData])
async def health_check(
    service: BotChatServiceProtocol = Injected(BotChatServiceProtocol),
):
    """Check Langfuse API connectivity. No auth required."""
    try:
        result = await service.health_check()
        success = result.status == "healthy"
        return ApiResponse(
            success=success,
            message="ok" if success else "Langfuse API unreachable",
            error_code=200 if success else 5003,
            data=result,
        )
    except Exception as e:
        return ApiResponse(success=False, message=str(e), error_code=5999)


@router.get("/{trace_id}", response_model=ApiResponse[ConversationDetail])
async def get_session(
    trace_id: str,
    log_source: str | None = Query(
        default=None,
        description="Data source: 'db' (default) or 'langfuse'",
    ),
    user: AuthenticatedUser = Depends(get_current_user),
    service: BotChatServiceProtocol = Injected(BotChatServiceProtocol),
):
    """Get a single bot conversation session detail with observation tree."""
    try:
        result = await service.get_session(trace_id=trace_id, owner_id=user.staffId, log_source=log_source)
        return ApiResponse(success=True, message="ok", data=result)
    except SessionNotFoundError as e:
        return ApiResponse(success=False, message=str(e), error_code=4004)
    except LangfuseAPIError as e:
        return ApiResponse(success=False, message=str(e), error_code=5999)
    except Exception as e:
        return ApiResponse(success=False, message=str(e), error_code=5999)
