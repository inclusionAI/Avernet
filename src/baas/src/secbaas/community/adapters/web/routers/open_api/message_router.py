"""Open API Message 路由

提供消息投递的 Open API 端点。
"""

import json
import time

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from secbaas.community.adapters.web.routers.open_api.dependencies import (
    _normalize_bot_id,
    get_bot_chat_context,
    validate_api_key,
    validate_policy,
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
from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.api.bot_runtime import (
    BotChatContext,
    BotNotAvailableError,
    BotNotFoundError,
    BotRunner,
    BotServiceError,
    TooManyRequestsError,
)
from secbaas.community.api.open_api import OpenAPICode, get_code_message
from secbaas.community.api.sse import (
    SseConverterFactory,
    SseEvent,
    convert_chunks_to_sse,
    with_sse_heartbeat,
)
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router-open-api")


router = APIRouter(prefix="/openapi/v1", tags=["Open API Message"])


@router.post(
    "/messages",
    response_model=MessageResponse,
    summary="消息投递",
    description="通过 Bearer Token 认证的 API Key 投递消息到指定 Bot",
    responses={
        200: {"description": "投递成功"},
        400: {"description": "参数错误"},
        401: {"description": "认证失败"},
        404: {"description": "Bot 不存在"},
        503: {"description": "Bot 不可用"},
        500: {"description": "服务内部错误"},
    },
)
@inject
async def deliver_message(
    request: MessageRequest,
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> MessageResponse:
    """消息投递端点

    Args:
        request: 消息投递请求
        api_key_record: 从 API Key 验证获取的记录
        context: 请求上下文（身份认证、调用者信息等）
        bot_runner: BotRunner 实例

    Returns:
        MessageResponse: 消息投递响应
    """
    if api_key_record.app_type not in ("system", "app"):
        logger.warning(f"deliver_message forbidden: app_type={api_key_record.app_type}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": OpenAPICode.FORBIDDEN,
                "message": get_code_message(OpenAPICode.FORBIDDEN),
            },
        )

    bot_id = _normalize_bot_id(request.bot_id)

    bot_id = validate_policy(api_key_record, bot_id)

    metadata = request.metadata or {}
    callback = None
    if request.callback_url is not None:
        metadata["callback_url"] = request.callback_url
        callback = "http_callback"

    logger.info(
        f"deliver_message: bot_id={bot_id}, app_id={api_key_record.app_id}, "
        f"api_key_prefix={api_key_record.api_key_prefix}"
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
            detail={"code": 50001, "message": f"服务内部错误: {str(e)}"},
        )


@router.post(
    "/messages/stream",
    summary="流式消息投递",
    description="通过 Bearer Token 认证的 API Key 投递消息到指定 Bot，以 SSE 流式返回结果",
    response_model=None,
    responses={
        200: {"description": "流式投递成功"},
        400: {"description": "参数错误"},
        401: {"description": "认证失败"},
        403: {"description": "无权限"},
        404: {"description": "Bot 不存在"},
        429: {"description": "请求过多"},
        503: {"description": "Bot 不可用"},
        500: {"description": "服务内部错误"},
    },
)
@inject
async def deliver_message_stream(
    request: StreamMessageRequest,
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
    converter_factory: SseConverterFactory = Depends(
        Provide[ApplicationContainer.services.stream_converter_factory]
    ),
) -> StreamingResponse:
    """流式消息投递端点

    与 deliver_message 使用相同的认证和校验逻辑，
    但调用 deliver_message_stream 获取 AsyncIterator[StreamChunk]，
    将每个 chunk 转换为 SSE 事件返回。
    """
    if api_key_record.app_type not in ("system", "app"):
        logger.warning(
            f"deliver_message_stream forbidden: app_type={api_key_record.app_type}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": OpenAPICode.FORBIDDEN,
                "message": get_code_message(OpenAPICode.FORBIDDEN),
            },
        )

    bot_id = _normalize_bot_id(request.bot_id)

    bot_id = validate_policy(api_key_record, bot_id)

    metadata = request.metadata or {}
    metadata["stream"] = "true"

    logger.info(
        f"deliver_message_stream: bot_id={bot_id}, app_id={api_key_record.app_id}, "
        f"api_key_prefix={api_key_record.api_key_prefix}"
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
            detail={"code": 50001, "message": f"服务内部错误: {str(e)}"},
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
    summary="查询消息结果",
    description="根据 message_id 查询消息投递的处理结果",
    responses={
        200: {"description": "查询成功"},
        401: {"description": "认证失败"},
        404: {"description": "消息不存在"},
    },
)
@inject
async def get_message_result(
    message_id: str,
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> MessageResultResponse:
    """查询消息结果端点

    Args:
        message_id: 消息 ID
        api_key_record: 从 API Key 验证获取的记录
        bot_runner: BotRunner 实例

    Returns:
        MessageResultResponse: 消息结果响应
    """
    try:
        logger.info(
            f"get_message_result: message_id={message_id}, api_key_prefix={api_key_record.api_key_prefix}"
        )

        if api_key_record.app_type not in ("system", "app"):
            logger.warning(
                f"get_message_result forbidden: app_type={api_key_record.app_type}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": OpenAPICode.FORBIDDEN,
                    "message": get_code_message(OpenAPICode.FORBIDDEN),
                },
            )

        message_info = bot_runner.get_result(message_id)

        validate_policy(api_key_record, message_info.bot_id)

        # 校验 message 归属，防止横向越权
        if message_info.api_key_prefix != api_key_record.api_key_prefix:
            logger.warning(
                f"get_message_result api_key mismatch: message_id={message_id}, "
                f"expected={api_key_record.api_key_prefix}, actual={message_info.api_key_prefix}"
            )
            return MessageResultResponse(
                code=OpenAPICode.BUSINESS_ERROR,
                message=f"Message not found: {message_id}",
                data=None,
            )

        # 构造结果数据
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

    except KeyError:
        logger.warning(f"get_message_result not found: message_id={message_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": f"Message not found: {message_id}"},
        )
