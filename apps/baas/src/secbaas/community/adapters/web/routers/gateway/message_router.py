"""Gateway Message Router

Provides JWT-authenticated message delivery endpoints.

Auth flow: JWT validation → resource_key lookup → verify bot_id mapping.
No per-caller API Key; BotChatContext is built from resource_key.
"""

import json
import time

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from secbaas.community.adapters.web.routers.gateway.dependencies import (
    GatewayAuthContext,
    check_bot_access,
    get_bot_chat_context,
    validate_jwt_token,
)
from secbaas.community.adapters.web.routers.open_api.model import (
    ExtraInfo,
    MessageRequest,
    MessageResponse,
    MessageResponseData,
    MessageResultData,
    MessageResultResponse,
    MessageResultResponseData,
    StreamMessageRequest,
)
from secbaas.community.api.api_gateway import ResourceKeyRepository
from secbaas.community.api.bot_runtime import (
    BotChatContext,
    BotNotAvailableError,
    BotNotFoundError,
    BotRunner,
    BotServiceError,
    TooManyRequestsError,
)
from secbaas.community.api.open_api import OpenAPICode
from secbaas.community.api.sse import (
    SseConverterFactory,
    SseEvent,
    convert_chunks_to_sse,
    with_sse_heartbeat,
)
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router-gateway")

router = APIRouter(prefix="/openapi/v1/chat", tags=["Chat / Messages"])


@router.post(
    "",
    response_model=MessageResponse,
    summary="Gateway message delivery",
    description="Deliver a message to a specified Bot using a JWT-authenticated request",
    responses={
        200: {"description": "Delivery successful"},
        400: {"description": "Invalid parameters"},
        401: {"description": "Authentication failed"},
        403: {"description": "Bot access denied"},
        404: {"description": "Bot not found"},
        503: {"description": "Bot unavailable"},
        500: {"description": "Internal server error"},
    },
)
@inject
async def deliver_message(
    request: MessageRequest,
    auth_ctx: GatewayAuthContext = Depends(validate_jwt_token),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
    resource_key_repository: ResourceKeyRepository = Depends(
        Provide[ApplicationContainer.repository.resource_key_repository]
    ),
) -> MessageResponse:
    """Gateway message delivery endpoint.

    JWT auth then verify bot_id access via resource_key mapping.
    """
    bot_id = check_bot_access(request.bot_id, auth_ctx, resource_key_repository)
    metadata = request.metadata or {}
    callback = None
    if request.callback_url is not None:
        metadata["callback_url"] = request.callback_url
        callback = "http_callback"

    logger.info(
        f"deliver_message: bot_id={bot_id}, resource_key={auth_ctx.resource_key}"
    )

    try:
        t_start = time.monotonic()
        message_id, inner_session_id = await bot_runner.deliver_message(
            bot_id=bot_id,
            message=request.message,
            context=context,
            metadata=metadata,
            callback=callback,
            message_id=request.message_id,
        )
        elapsed_ms = (time.monotonic() - t_start) * 1000

        logger.info(
            f"deliver_message success: bot_id={bot_id}, message_id={message_id}, "
            f"session_id={inner_session_id}, elapsed={elapsed_ms:.1f}ms"
        )

        if inner_session_id is None:
            return MessageResponse(
                code=OpenAPICode.BUSINESS_ERROR,
                message="Session not exist",
                data=MessageResponseData(message_id=message_id),
            )

        return MessageResponse(
            code=0,
            message="success",
            data=MessageResponseData(
                message_id=message_id, session_id=inner_session_id
            ),
        )

    except BotNotFoundError as e:
        logger.warning(f"deliver_message bot not found: bot_id={bot_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except TooManyRequestsError as e:
        logger.warning(f"deliver_message too many requests: bot_id={bot_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": 42901, "message": str(e)},
        )
    except BotNotAvailableError as e:
        logger.warning(f"deliver_message bot not available: bot_id={bot_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": 60001, "message": str(e)},
        )
    except BotServiceError as e:
        logger.error(f"deliver_message bot service error: bot_id={bot_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": OpenAPICode.BUSINESS_ERROR, "message": str(e)},
        )
    except Exception as e:
        logger.error(
            f"deliver_message unexpected error: bot_id={bot_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"Internal server error: {str(e)}"},
        )


@router.post(
    "/stream",
    summary="Gateway streaming message delivery",
    description="Deliver a message to a specified Bot using a JWT-authenticated request, returning results as an SSE stream",
    response_model=None,
    responses={
        200: {"description": "Stream delivery successful"},
        400: {"description": "Invalid parameters"},
        401: {"description": "Authentication failed"},
        403: {"description": "Bot access denied"},
        404: {"description": "Bot not found"},
        429: {"description": "Too many requests"},
        503: {"description": "Bot unavailable"},
        500: {"description": "Internal server error"},
    },
)
@inject
async def deliver_message_stream(
    request: StreamMessageRequest,
    auth_ctx: GatewayAuthContext = Depends(validate_jwt_token),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
    converter_factory: SseConverterFactory = Depends(
        Provide[ApplicationContainer.services.stream_converter_factory]
    ),
    resource_key_repository: ResourceKeyRepository = Depends(
        Provide[ApplicationContainer.repository.resource_key_repository]
    ),
) -> StreamingResponse:
    """Gateway streaming message delivery endpoint."""
    bot_id = check_bot_access(request.bot_id, auth_ctx, resource_key_repository)
    metadata = request.metadata or {}
    metadata["stream"] = "true"

    logger.info(
        f"deliver_message_stream: bot_id={bot_id}, resource_key={auth_ctx.resource_key}"
    )

    try:
        message_id, session_id, chunk_iter = await bot_runner.deliver_message_stream(
            bot_id=bot_id,
            message=request.message,
            context=context,
            metadata=metadata,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": OpenAPICode.BUSINESS_ERROR, "message": str(e)},
        )
    except BotNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except TooManyRequestsError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": 42901, "message": str(e)},
        )
    except BotNotAvailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": 60001, "message": str(e)},
        )
    except BotServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": OpenAPICode.BUSINESS_ERROR, "message": str(e)},
        )
    except Exception as e:
        logger.error(
            f"deliver_message_stream unexpected error: bot_id={bot_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"Internal server error: {str(e)}"},
        )

    converter = converter_factory.create("default")

    ready_sse = SseEvent(
        event="ready",
        data=json.dumps(
            {"message_id": message_id, "session_id": session_id},
            ensure_ascii=False,
        ),
    ).to_sse()

    def on_error(e: Exception) -> str:
        logger.exception(
            "deliver_message_stream chunk error: message_id=%s", message_id
        )
        return SseEvent(
            event="error",
            data=json.dumps(
                {"message_id": message_id, "error": str(e)},
                ensure_ascii=False,
            ),
        ).to_sse()

    return StreamingResponse(
        with_sse_heartbeat(
            convert_chunks_to_sse(
                chunk_iter,
                converter,
                message_id,
                prefix=[ready_sse],
                on_error=on_error,
            )
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/messages/{message_id}",
    response_model=MessageResultResponse,
    summary="Gateway query message result",
    description="Query the processing result of a message delivery by message_id (JWT-authenticated)",
    responses={
        200: {"description": "Query successful"},
        401: {"description": "Authentication failed"},
        403: {"description": "Bot access denied"},
        404: {"description": "Message not found"},
    },
)
@inject
async def get_message_result(
    message_id: str,
    auth_ctx: GatewayAuthContext = Depends(validate_jwt_token),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
    resource_key_repository: ResourceKeyRepository = Depends(
        Provide[ApplicationContainer.repository.resource_key_repository]
    ),
) -> MessageResultResponse:
    """Gateway message result query endpoint.

    Verifies that the message's bot_id is authorized under resource_key.
    """
    logger.info(f"get_message_result: message_id={message_id}")

    try:
        message_info = bot_runner.get_result(message_id)
    except KeyError:
        logger.warning(f"get_message_result not found: message_id={message_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": f"Message not found: {message_id}"},
        )

    check_bot_access(message_info.bot_id, auth_ctx, resource_key_repository)

    result_data = None
    if message_info.result_content:
        extra = None
        if message_info.result_extra:
            extra = ExtraInfo(usage=message_info.result_extra.get("usage"))
        result_data = MessageResultData(
            content=message_info.result_content,
            extra=extra,
        )

    if message_info.metadata and "session_id" in message_info.metadata:
        session_id = message_info.metadata.get("session_id")
    elif message_info.result_extra and "session_id" in message_info.result_extra:
        session_id = message_info.result_extra.get("session_id")
    else:
        session_id = ""

    logger.info(
        f"get_message_result success: message_id={message_id}, status={message_info.status}"
    )

    return MessageResultResponse(
        code=0,
        message="success",
        data=MessageResultResponseData(
            message_id=message_info.run_id,
            bot_id=message_info.bot_id,
            session_id=session_id,
            status=message_info.status,
            created_at=message_info.gmt_create,
            completed_at=message_info.completed_at,
            result=result_data,
            error=message_info.error,
        ),
    )
