"""Gateway Session Router

Provides JWT-authenticated session query endpoints.

JWT auth then verify bot_id access via resource_key mapping.
"""

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, status

from secbaas.community.adapters.web.routers.gateway.dependencies import (
    GatewayAuthContext,
    check_bot_access,
    get_bot_chat_context,
    validate_jwt_token,
)
from secbaas.community.adapters.web.routers.open_api.model import (
    MessageItem,
    SessionListItem,
    SessionListResponse,
    SessionListResponseData,
    SessionMessagesResponse,
    SessionMessagesResponseData,
    SessionQueryResponse,
    SessionQueryResponseData,
)
from secbaas.community.api.api_gateway import ResourceKeyRepository
from secbaas.community.api.bot_runtime import (
    BotBindingNotFoundError,
    BotChatContext,
    BotNotFoundError,
    BotRunner,
    BotServiceError,
    SessionNotFoundError,
)
from secbaas.community.api.open_api import OpenAPICode
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router-gateway")

router = APIRouter(prefix="/openapi/v1/chat", tags=["Chat / Sessions"])


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="Gateway list sessions",
    description="List sessions for a specified Bot using a JWT-authenticated request",
    responses={
        200: {"description": "Query successful"},
        401: {"description": "Authentication failed"},
        403: {"description": "Bot access denied"},
        404: {"description": "Bot not found"},
        500: {"description": "Internal server error"},
    },
)
@inject
async def list_sessions(
    bot_id: str = Query(
        ...,
        description="Bot ID, format: bot_id:staff_no",
    ),
    user_id: str | None = Query(
        default=None,
        description="Optional user ID to filter sessions by a specific user",
    ),
    lifecycle_stage: str = Query(
        default="online",
        description="Bot lifecycle stage. Options: online, verify, draft, all",
    ),
    limit: int = Query(
        default=20, ge=1, le=100, description="Maximum number of sessions to return"
    ),
    offset: int = Query(default=0, ge=0, description="Number of sessions to skip"),
    auth_ctx: GatewayAuthContext = Depends(validate_jwt_token),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
    resource_key_repository: ResourceKeyRepository = Depends(
        Provide[ApplicationContainer.repository.resource_key_repository]
    ),
) -> SessionListResponse:
    """Gateway sessions list endpoint."""
    resolved_bot_id = check_bot_access(bot_id, auth_ctx, resource_key_repository)

    logger.info(
        f"list_sessions: bot_id={resolved_bot_id}, user_id={user_id}, "
        f"lifecycle_stage={lifecycle_stage}, limit={limit}, offset={offset}, "
        f"resource_key={auth_ctx.resource_key}"
    )

    try:
        # Fetch limit+1 to determine has_more without extra count
        sessions = await bot_runner.list_sessions(
            bot_id=resolved_bot_id,
            context=context,
            metadata={"bot_options": {"lifecycle_stage": lifecycle_stage}},
            user_id=user_id,
            limit=limit + 1,
            offset=offset,
        )

        has_more = len(sessions) > limit
        sessions = sessions[:limit]
        total = offset + len(sessions) + (1 if has_more else 0)

        session_items = [
            SessionListItem(
                session_id=s.session_id,
                bot_id=s.bot_id,
                title=s.title,
                status=s.status,
                message_count=s.message_count,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in sessions
        ]

        logger.info(
            f"list_sessions success: bot_id={resolved_bot_id}, "
            f"returned={len(session_items)}, has_more={has_more}"
        )

        return SessionListResponse(
            code=0,
            message="success",
            data=SessionListResponseData(
                items=session_items,
                total=total,
                has_more=has_more,
            ),
        )

    except BotBindingNotFoundError as e:
        logger.warning(
            f"list_sessions binding not found: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except BotNotFoundError as e:
        logger.warning(
            f"list_sessions bot not found: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except BotServiceError as e:
        logger.error(
            f"list_sessions bot service error: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": OpenAPICode.BUSINESS_ERROR, "message": str(e)},
        )
    except Exception as e:
        logger.error(
            f"list_sessions unexpected error: bot_id={resolved_bot_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"Internal server error: {str(e)}"},
        )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionQueryResponse,
    summary="Gateway query session",
    description="Query a specific session's information using a JWT-authenticated request",
    responses={
        200: {"description": "Query successful"},
        401: {"description": "Authentication failed"},
        403: {"description": "Bot access denied"},
        404: {"description": "Session not found"},
        500: {"description": "Internal server error"},
    },
)
@inject
async def get_session(
    session_id: str,
    bot_id: str = Query(
        ...,
        description="Bot ID, format: bot_id:staff_no",
    ),
    lifecycle_stage: str = Query(
        default="online",
        description="Bot lifecycle stage. Options: online, verify, draft, all",
    ),
    auth_ctx: GatewayAuthContext = Depends(validate_jwt_token),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
    resource_key_repository: ResourceKeyRepository = Depends(
        Provide[ApplicationContainer.repository.resource_key_repository]
    ),
) -> SessionQueryResponse:
    """Gateway session query endpoint."""
    resolved_bot_id = check_bot_access(bot_id, auth_ctx, resource_key_repository)

    logger.info(
        f"get_session: session_id={session_id}, bot_id={resolved_bot_id}, "
        f"lifecycle_stage={lifecycle_stage}, resource_key={auth_ctx.resource_key}"
    )

    try:
        session_info = await bot_runner.get_session_info(
            bot_id=resolved_bot_id,
            session_id=session_id,
            context=context,
            metadata={"bot_options": {"lifecycle_stage": lifecycle_stage}},
        )

        return SessionQueryResponse(
            code=0,
            message="success",
            data=SessionQueryResponseData(
                session_id=session_info.session_id,
                bot_id=session_info.bot_id,
                status=session_info.status,
                created_at=session_info.created_at,
                updated_at=session_info.updated_at,
            ),
        )

    except SessionNotFoundError as e:
        logger.warning(
            f"get_session session not found: session_id={session_id}, bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": OpenAPICode.BUSINESS_ERROR,
                "message": str(e),
            },
        )
    except BotBindingNotFoundError as e:
        logger.warning(
            f"get_session binding not found: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except BotNotFoundError as e:
        logger.warning(
            f"get_session bot not found: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except BotServiceError as e:
        logger.error(
            f"get_session bot service error: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": OpenAPICode.BUSINESS_ERROR, "message": str(e)},
        )
    except Exception as e:
        logger.error(
            f"get_session unexpected error: session_id={session_id}, bot_id={resolved_bot_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"Internal server error: {str(e)}"},
        )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=SessionMessagesResponse,
    summary="Gateway query session messages",
    description="Query a specific session's message list using a JWT-authenticated request",
    responses={
        200: {"description": "Query successful"},
        401: {"description": "Authentication failed"},
        403: {"description": "Bot access denied"},
        404: {"description": "Session not found"},
        500: {"description": "Internal server error"},
    },
)
@inject
async def get_session_messages(
    session_id: str,
    limit: int = Query(
        default=1000, ge=1, le=1000, description="Maximum number of messages to return"
    ),
    bot_id: str = Query(
        ...,
        description="Bot ID, format: bot_id:staff_no",
    ),
    lifecycle_stage: str = Query(
        default="online",
        description="Bot lifecycle stage. Options: online, verify, draft, all",
    ),
    auth_ctx: GatewayAuthContext = Depends(validate_jwt_token),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
    resource_key_repository: ResourceKeyRepository = Depends(
        Provide[ApplicationContainer.repository.resource_key_repository]
    ),
) -> SessionMessagesResponse:
    """Gateway session messages query endpoint."""
    resolved_bot_id = check_bot_access(bot_id, auth_ctx, resource_key_repository)

    logger.info(
        f"get_session_messages: session_id={session_id}, bot_id={resolved_bot_id}, "
        f"lifecycle_stage={lifecycle_stage}, limit={limit}, "
        f"resource_key={auth_ctx.resource_key}"
    )

    try:
        messages = await bot_runner.get_session_messages(
            bot_id=resolved_bot_id,
            session_id=session_id,
            context=context,
            metadata={"bot_options": {"lifecycle_stage": lifecycle_stage}},
        )

        total = len(messages)
        sliced = messages[:limit]
        has_more = total > limit

        message_items = [
            MessageItem(
                id=msg.id,
                session_id=msg.session_id,
                role=msg.role,
                content=msg.content,
                meta=msg.meta,
                created_at=msg.created_at,
                history_meta=msg.history_meta,
            )
            for msg in sliced
        ]

        logger.info(
            f"get_session_messages success: session_id={session_id}, "
            f"total={total}, returned={len(message_items)}, has_more={has_more}"
        )

        return SessionMessagesResponse(
            code=0,
            message="success",
            data=SessionMessagesResponseData(
                session_id=session_id,
                messages=message_items,
                total=total,
                has_more=has_more,
            ),
        )

    except SessionNotFoundError as e:
        logger.warning(
            f"get_session_messages session not found: session_id={session_id}, bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": OpenAPICode.BUSINESS_ERROR,
                "message": str(e),
            },
        )
    except BotBindingNotFoundError as e:
        logger.warning(
            f"get_session_messages binding not found: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except BotNotFoundError as e:
        logger.warning(
            f"get_session_messages bot not found: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except BotServiceError as e:
        logger.error(
            f"get_session_messages bot service error: bot_id={resolved_bot_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": OpenAPICode.BUSINESS_ERROR, "message": str(e)},
        )
    except Exception as e:
        logger.error(
            f"get_session_messages unexpected error: session_id={session_id}, bot_id={resolved_bot_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"Internal server error: {str(e)}"},
        )
