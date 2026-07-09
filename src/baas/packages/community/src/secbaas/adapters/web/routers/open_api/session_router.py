"""Open API Session 路由

提供会话查询的 Open API 端点。
"""

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, status

from secbaas.adapters.web.routers.open_api.dependencies import (
    get_bot_chat_context,
    resolve_bot_id_from_api_key,
    validate_api_key,
    validate_policy,
)
from secbaas.adapters.web.routers.open_api.model import (
    MessageItem,
    SessionMessagesResponse,
    SessionMessagesResponseData,
    SessionQueryResponse,
    SessionQueryResponseData,
)
from secbaas.api.api_gateway import APIKeyRecord
from secbaas.api.bot_runtime import (
    BotBindingNotFoundError,
    BotChatContext,
    BotNotFoundError,
    BotRunner,
    BotServiceError,
    SessionNotFoundError,
)
from secbaas.api.open_api import OpenAPICode, get_code_message
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

logger = get_logger("router-open-api")

router = APIRouter(prefix="/openapi/v1", tags=["Open API Sessions"])


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
    summary="查询会话",
    description="通过 Bearer Token 认证的 API Key 查询指定会话信息",
    responses={
        200: {"description": "查询成功"},
        401: {"description": "认证失败"},
        403: {"description": "无权限访问"},
        404: {"description": "会话不存在"},
        500: {"description": "服务内部错误"},
    },
)
@inject
async def get_session(
    session_id: str,
    bot_id: str | None = Query(
        default=None,
        description="Bot ID，格式为 bot_id 或 default:staff_no，不传则从 API Key 解析",
    ),
    lifecycle_stage: str = Query(
        default="online",
        description="Bot 生命周期阶段，可选值: online, verify, draft, all",
    ),
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> SessionQueryResponse:
    """查询会话端点

    Args:
        session_id: 会话 ID
        bot_id: Bot ID，格式为 bot_id 或 default:staff_no，不传则从 API Key 解析
        lifecycle_stage: Bot 生命周期阶段，可选值: online, verify, draft, all，默认 online
        api_key_record: 从 API Key 验证获取的记录
        context: 请求上下文（身份认证、调用者信息等）
        bot_runner: BotRunner 实例

    Returns:
        SessionQueryResponse: 会话查询响应
    """
    # 只有 app_type=bot 时才可从 API Key 解析 bot_id，其余类型必须显式传入
    if bot_id:
        resolved_bot_id = bot_id
    elif api_key_record.app_type == "bot":
        resolved_bot_id = resolve_bot_id_from_api_key(api_key_record)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OpenAPICode.BUSINESS_ERROR,
                "message": "bot_id 是必填参数",
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
            detail={"code": 50001, "message": f"服务内部错误: {str(e)}"},
        )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=SessionMessagesResponse,
    summary="查询会话消息",
    description="通过 Bearer Token 认证的 API Key 查询指定会话的消息列表",
    responses={
        200: {"description": "查询成功"},
        401: {"description": "认证失败"},
        403: {"description": "无权限访问"},
        404: {"description": "会话不存在"},
        500: {"description": "服务内部错误"},
    },
)
@inject
async def get_session_messages(
    session_id: str,
    limit: int = Query(default=1000, ge=1, le=1000, description="返回消息数量上限"),
    bot_id: str | None = Query(
        default=None,
        description="Bot ID，格式为 bot_id 或 default:staff_no，不传则从 API Key 解析",
    ),
    lifecycle_stage: str = Query(
        default="online",
        description="Bot 生命周期阶段，可选值: online, verify, draft, all",
    ),
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> SessionMessagesResponse:
    """查询会话消息端点

    Args:
        session_id: 会话 ID
        limit: 返回消息数量上限，默认 1000，范围 1-1000
        bot_id: Bot ID，格式为 bot_id 或 default:staff_no，不传则从 API Key 解析
        lifecycle_stage: Bot 生命周期阶段，可选值: online, verify, draft, all，默认 online
        api_key_record: 从 API Key 验证获取的记录
        context: 请求上下文（身份认证、调用者信息等）
        bot_runner: BotRunner 实例

    Returns:
        SessionMessagesResponse: 会话消息列表响应
    """
    # 只有 app_type=bot 时才可从 API Key 解析 bot_id，其余类型必须显式传入
    if bot_id:
        resolved_bot_id = bot_id
    elif api_key_record.app_type == "bot":
        resolved_bot_id = resolve_bot_id_from_api_key(api_key_record)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": OpenAPICode.BUSINESS_ERROR,
                "message": "bot_id 是必填参数",
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
            detail={"code": 50001, "message": f"服务内部错误: {str(e)}"},
        )
