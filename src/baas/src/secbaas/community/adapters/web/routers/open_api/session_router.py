"""Open API Session Router

Provides session query Open API endpoints.
"""

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, status

from secbaas.community.adapters.web.routers.open_api.dependencies import (
    get_bot_chat_context,
    resolve_bot_id_from_api_key,
    validate_api_key,
    validate_policy,
)
from secbaas.community.adapters.web.routers.open_api.model import (
    MessageItem,
    SessionMessagesResponse,
    SessionMessagesResponseData,
    SessionQueryResponse,
    SessionQueryResponseData,
)
from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.api.bot_runtime import (
    BotBindingNotFoundError,
    BotChatContext,
    BotNotFoundError,
    BotRunner,
    BotServiceError,
    SessionNotFoundError,
)
from secbaas.community.api.open_api import OpenAPICode, get_code_message
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router-open-api")

router = APIRouter(prefix="/openapi/v1", tags=["sessions"])


def _check_app_type(api_key_record: APIKeyRecord) -> None:
    """Check that the API key app_type is authorized for session endpoints."""
    if api_key_record.app_type not in ("system", "app", "bot"):
        logger.warning(
            f"Session endpoint forbidden: app_type={api_key_record.app_type}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": OpenAPICode.FORBIDDEN,
                "message": get_code_message(OpenAPICode.FORBIDDEN),
            },
        )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionQueryResponse,
    summary="Query session",
    description="Query a specific session's information using a Bearer Token-authenticated API Key",
    responses={
        200: {"description": "Query successful"},
        401: {"description": "Authentication failed"},
        403: {"description": "Access denied"},
        404: {"description": "Session not found"},
        500: {"description": "Internal server error"},
    },
)
@inject
async def get_session(
    session_id: str,
    bot_id: str | None = Query(
        default=None,
        description="Bot ID, format: bot_id or default:staff_no. If omitted, resolved from the API Key.",
    ),
    lifecycle_stage: str = Query(
        default="online",
        description="Bot lifecycle stage. Options: online, verify, draft, all",
    ),
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> SessionQueryResponse:
    """Query session endpoint

    Args:
        session_id: Session ID
        bot_id: Bot ID, format: bot_id or default:staff_no. If omitted, resolved from the API Key.
        lifecycle_stage: Bot lifecycle stage. Options: online, verify, draft, all. Default: online
        api_key_record: Record obtained from API Key validation
        context: Request context (authentication, caller identity, etc.)
        bot_runner: BotRunner instance

    Returns:
        SessionQueryResponse: Session query response
    """
    # Only app_type=bot can resolve bot_id from the API Key; other types must pass it explicitly
    if bot_id:
        resolved_bot_id = bot_id
    elif api_key_record.app_type == "bot":
        resolved_bot_id = resolve_bot_id_from_api_key(api_key_record)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OpenAPICode.BUSINESS_ERROR,
                "message": "bot_id is a required parameter",
            },
        )

    _check_app_type(api_key_record)
    if api_key_record.app_type != "bot":
        resolved_bot_id = validate_policy(api_key_record, resolved_bot_id)

    logger.info(
        f"get_session: session_id={session_id}, bot_id={resolved_bot_id}, "
        f"lifecycle_stage={lifecycle_stage}, "
        f"api_key_prefix={api_key_record.api_key_prefix}"
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
    summary="Query session messages",
    description="Query a specific session's message list using a Bearer Token-authenticated API Key",
    responses={
        200: {"description": "Query successful"},
        401: {"description": "Authentication failed"},
        403: {"description": "Access denied"},
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
    bot_id: str | None = Query(
        default=None,
        description="Bot ID, format: bot_id or default:staff_no. If omitted, resolved from the API Key.",
    ),
    lifecycle_stage: str = Query(
        default="online",
        description="Bot lifecycle stage. Options: online, verify, draft, all",
    ),
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> SessionMessagesResponse:
    """Query session messages endpoint

    Args:
        session_id: Session ID
        limit: Maximum number of messages to return. Default 1000, range 1-1000
        bot_id: Bot ID, format: bot_id or default:staff_no. If omitted, resolved from the API Key.
        lifecycle_stage: Bot lifecycle stage. Options: online, verify, draft, all. Default: online
        api_key_record: Record obtained from API Key validation
        context: Request context (authentication, caller identity, etc.)
        bot_runner: BotRunner instance

    Returns:
        SessionMessagesResponse: Session messages list response
    """
    # Only app_type=bot can resolve bot_id from the API Key; other types must pass it explicitly
    if bot_id:
        resolved_bot_id = bot_id
    elif api_key_record.app_type == "bot":
        resolved_bot_id = resolve_bot_id_from_api_key(api_key_record)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OpenAPICode.BUSINESS_ERROR,
                "message": "bot_id is a required parameter",
            },
        )

    _check_app_type(api_key_record)
    if api_key_record.app_type != "bot":
        resolved_bot_id = validate_policy(api_key_record, resolved_bot_id)

    logger.info(
        f"get_session_messages: session_id={session_id}, bot_id={resolved_bot_id}, "
        f"lifecycle_stage={lifecycle_stage}, limit={limit}, "
        f"api_key_prefix={api_key_record.api_key_prefix}"
    )

    try:
        messages = await bot_runner.get_session_messages(
            bot_id=resolved_bot_id,
            session_id=session_id,
            context=context,
            metadata={"bot_options": {"lifecycle_stage": lifecycle_stage}},
        )

        # Apply limit to slice results
        total = len(messages)
        sliced = messages[:limit]
        has_more = total > limit

        # Transform MessageInfo -> MessageItem
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
