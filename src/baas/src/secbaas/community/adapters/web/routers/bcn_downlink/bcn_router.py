"""BCN 下行协议路由

提供 BCN -> Provider 的下行接口:
  - POST /bcn/v1/downlink  (chat.send / chat.inject / chat.history)

本文件只负责:
  1. HTTP 认证与协议版本校验
  2. 请求体解析与 Pydantic 校验
  3. 调用 core/service/bcn 层服务
  4. 响应构造

异常处理遵循 FastAPI 最佳实践:
  - handler 内直接抛领域异常 (BcnError)
  - 通过 router 级别 exception handler 统一映射为 HTTP 响应
  - 不再手动 try/except + raise HTTPException

业务逻辑全部委托给 core/service/bcn 层的 BcnDownlinkService。

参考: BCN Bot 下行连接接入方案 (内部文档)
"""

import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from secbaas.community.adapters.web.routers.bcn_downlink.bcn_model import (
    BcnErrorDetail,
    BcnErrorResponse,
    ChatHistoryRequest,
    ChatHistorySuccessResponse,
    ChatInjectRequest,
    ChatInjectSuccessResponse,
    ChatSendRequest,
    ChatSendSuccessResponse,
)
from secbaas.community.adapters.web.routers.bcn_downlink.bcn_model import (
    HistoryMessage as BcnHistoryMessage,
)
from secbaas.community.api.bcn import (
    BcnBotNotFoundError,
    BcnDownlinkService,
    BcnError,
    BcnInvalidRequestError,
    BcnUnauthorizedError,
    BcnUnsupportedMethodError,
    ChatHistoryInput,
    ChatInjectInput,
    ChatSendInput,
)
from secbaas.community.api.bot_runtime import BotBindingNotFoundError
from secbaas.community.api.sse import (
    SseConverterFactory,
    SseEvent,
    StreamChunk,
    convert_chunks_to_sse,
    with_sse_heartbeat,
)
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger
from secbaas.community.spi.secret import SecretStorePlugin

logger = get_logger("router-open-api")

router = APIRouter(prefix="/bcn", tags=["BCN Downlink"])


# ─────────────────────────── Exception Handler ───────────────────────────


async def bcn_exception_handler(request: Request, exc: BcnError) -> JSONResponse:
    """BCN 领域异常 → HTTP 错误响应的统一映射

    所有 BcnError 子类自带 http_status / error_code / retryable，
    无需 isinstance 分支，一张表搞定。

    此函数需在 app.py 中通过 ``app.add_exception_handler(BcnError, bcn_exception_handler)``
    注册到 FastAPI 应用实例。BcnError 是 DomainError 的子类，
    FastAPI 会优先匹配更具体的异常类型。
    """
    if exc.http_status >= 500:
        logger.error(f"BcnError: {exc.error_code} - {exc.message}", exc_info=True)
    else:
        logger.warning(f"BcnError: {exc.error_code} - {exc.message}")

    body = BcnErrorResponse(
        error=BcnErrorDetail(
            code=exc.error_code,
            message=exc.message,
            retryable=exc.retryable,
        )
    ).model_dump()
    return JSONResponse(status_code=exc.http_status, content=body)


# ─────────────────────────── 认证依赖 ───────────────────────────


@inject
def validate_bcn_token(
    authorization: str | None = Header(None),
    secret_plugin: SecretStorePlugin = Depends(
        Provide[ApplicationContainer.plugins.secret_plugin]
    ),
) -> str:
    """校验 BCN 下行请求的 Authorization Token

    认证失败直接抛 BcnUnauthorizedError，由 exception handler 统一处理。
    """
    if not authorization:
        raise BcnUnauthorizedError("Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise BcnUnauthorizedError("Invalid Authorization header format")

    expected_token = os.getenv("BCS_BAAS_DOWNLINK_TOKEN", "")
    if not expected_token:
        try:
            expected_token = secret_plugin.get_secret(
                "other_manual_secbaas_bcn_to_provider_token"
            )
        except RuntimeError:
            # Local stub secret storage has no BCN credential by default.
            # Preserve the legacy local behaviour unless the opt-in singlebox
            # bridge explicitly supplies its process-local credential.
            expected_token = ""
    if not expected_token:
        return parts[1]

    if parts[1] != expected_token:
        raise BcnUnauthorizedError("Invalid token")

    return parts[1]


# ─────────────────────────── Method 分发表 ───────────────────────────

_METHOD_DISPATCH: dict[
    str,
    tuple[type[BaseModel], Callable[[Any, BcnDownlinkService], Any]],
] = {}

_METHOD_STREAM_DISPATCH: dict[
    str,
    tuple[type[BaseModel], Callable[[Any, BcnDownlinkService, Any], Any]],
] = {}


def _register(method: str, req_model: type[BaseModel]):
    """装饰器：将 (RequestModel, handler) 注册到 _METHOD_DISPATCH"""

    def wrapper(fn):
        _METHOD_DISPATCH[method] = (req_model, fn)
        return fn

    return wrapper


def _register_stream(method: str, req_model: type[BaseModel]):
    """装饰器：将 (RequestModel, stream handler) 注册到 _METHOD_STREAM_DISPATCH"""

    def wrapper(fn):
        _METHOD_STREAM_DISPATCH[method] = (req_model, fn)
        return fn

    return wrapper


# ─────────────────────────── 下行统一入口 ───────────────────────────


@router.post(
    "/downlink",
    summary="BCN 下行统一入口",
    description="接收 BCN 下行请求 (chat.send / chat.inject / chat.history)，根据 body.method 分发",
    response_model=None,
    responses={
        200: {"description": "请求处理成功"},
        400: {"description": "请求格式错误"},
        401: {"description": "认证失败"},
        404: {"description": "Bot 或会话不存在"},
        409: {"description": "幂等键冲突"},
        412: {"description": "协议版本不兼容"},
        429: {"description": "流控"},
        500: {"description": "服务内部错误"},
        501: {"description": "不支持的 method"},
        503: {"description": "服务暂不可用"},
    },
)
@inject
async def bcn_downlink(
    request: Request,
    _token: str = Depends(validate_bcn_token),
    service: BcnDownlinkService = Depends(
        Provide[ApplicationContainer.services.bcn_downlink_service]
    ),
    converter_factory: SseConverterFactory = Depends(
        Provide[ApplicationContainer.services.stream_converter_factory]
    ),
) -> (
    ChatSendSuccessResponse
    | ChatInjectSuccessResponse
    | ChatHistorySuccessResponse
    | StreamingResponse
):
    """BCN 下行统一入口

    根据 body.method 分发到不同的处理逻辑:
    - chat.send: 请求 Bot 对当前会话轮次进行响应
      - extensions.response_mode == "stream" 时返回 SSE 流
      - 否则快速返回 200 OK，异步执行后通过 uplink 回调
    - chat.inject: 向 Bot 注入消息（不触发推理）
    - chat.history: 查询聊天历史
    """
    body = await request.json()
    method = body.get("method", "")

    # BCN 未传 id 时，在 Pydantic 校验前补一个 UUID
    if not body.get("id"):
        body["id"] = str(uuid.uuid4())

    # 通过 X-BCN-TRANSPORT header 判断响应模式：sse 走流式，json（或缺省）走普通
    transport = request.headers.get("x-bcn-transport", "json").lower()
    is_stream = transport == "sse"

    # stream 模式仅支持 chat.send，其他 method 直接 400
    if is_stream and method != "chat.send":
        raise BcnInvalidRequestError(
            f"Stream mode (X-BCN-TRANSPORT=sse) only supports chat.send, got method={method}"
        )

    if is_stream:
        stream_entry = _METHOD_STREAM_DISPATCH[method]
        req_model, stream_dispatcher = stream_entry
        req = req_model.model_validate(body)
        return await stream_dispatcher(req, service, converter_factory)

    # 普通分支：查 method 分发表
    entry = _METHOD_DISPATCH.get(method)
    if entry is None:
        raise BcnUnsupportedMethodError(method)

    req_model, dispatcher = entry
    req = req_model.model_validate(body)
    return await dispatcher(req, service)


# ─────────────────────────── chat.send (stream) ───────────────────────────


@_register_stream("chat.send", ChatSendRequest)
async def _dispatch_chat_send_stream(
    req: ChatSendRequest,
    service: BcnDownlinkService,
    converter_factory: SseConverterFactory,
) -> StreamingResponse:
    """chat.send SSE 流式分发

    构造领域输入，调用 service.handle_chat_send_stream 获取原始 StreamChunk 流，
    通过 converter_factory.create() 获取 converter 实例，逐个 chunk 转换为 SseEvent，
    再包装为 StreamingResponse 返回。

    错误处理：BcnError 在 SSE 流中作为 error 事件发出后关闭流，
    不走 JSONResponse exception handler。
    """
    input_ = ChatSendInput(
        run_id=req.id,
        session_id=req.session_id,
        bcn_group_id=req.bcn_group_id,
        to_bot=req.to_bot.to_domain(),
        from_ref=req.from_.to_domain(),
        message=req.message.to_domain(),
        timeout_ms=req.timeout_ms,
        extensions=req.extensions,
    )

    try:
        chunk_iter: AsyncIterator[StreamChunk] = await service.handle_chat_send_stream(
            input_
        )
    except BotBindingNotFoundError as exc:
        raise BcnBotNotFoundError(provider_bot_ref=exc.bot_id) from exc
    except ValueError as exc:
        raise BcnInvalidRequestError(str(exc)) from exc

    converter = converter_factory.create("default")

    def on_error(e: Exception) -> str:
        logger.exception("[chat.send.stream] Unexpected error: run_id=%s", req.id)
        return _build_sse_error(req.id, "INTERNAL_ERROR", str(e), False)

    return StreamingResponse(
        with_sse_heartbeat(
            convert_chunks_to_sse(chunk_iter, converter, req.id, on_error=on_error)
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────── chat.send ───────────────────────────


@_register("chat.send", ChatSendRequest)
async def _dispatch_chat_send(
    req: ChatSendRequest,
    service: BcnDownlinkService,
) -> ChatSendSuccessResponse:
    """chat.send: Pydantic -> 领域模型 -> 调用 service -> 响应

    领域异常 (BcnError / BotBindingNotFoundError) 直接上抛，
    由 exception handler 统一映射。
    """
    input_ = ChatSendInput(
        run_id=req.id,
        session_id=req.session_id,
        bcn_group_id=req.bcn_group_id,
        to_bot=req.to_bot.to_domain(),
        from_ref=req.from_.to_domain(),
        message=req.message.to_domain(),
        timeout_ms=req.timeout_ms,
        extensions=req.extensions,
    )

    try:
        result = await service.handle_chat_send(input_)
    except BotBindingNotFoundError as exc:
        raise BcnBotNotFoundError(provider_bot_ref=exc.bot_id) from exc

    return ChatSendSuccessResponse(ok=result.ok)


# ─────────────────────────── chat.inject ───────────────────────────


@_register("chat.inject", ChatInjectRequest)
async def _dispatch_chat_inject(
    req: ChatInjectRequest,
    service: BcnDownlinkService,
) -> ChatInjectSuccessResponse:
    """chat.inject: Pydantic -> 领域模型 -> 调用 service -> 响应"""
    input_ = ChatInjectInput(
        id=req.id,
        session_id=req.session_id,
        bcn_group_id=req.bcn_group_id,
        to_bot=req.to_bot.to_domain(),
        from_ref=req.from_.to_domain(),
        message=req.message.to_domain(),
        timeout_ms=req.timeout_ms,
    )

    try:
        result = await service.handle_chat_inject(input_)
    except BotBindingNotFoundError as exc:
        raise BcnBotNotFoundError(provider_bot_ref=exc.bot_id) from exc

    return ChatInjectSuccessResponse(ok=result.ok)


# ─────────────────────────── chat.history ───────────────────────────


@_register("chat.history", ChatHistoryRequest)
async def _dispatch_chat_history(
    req: ChatHistoryRequest,
    service: BcnDownlinkService,
) -> ChatHistorySuccessResponse:
    """chat.history: Pydantic -> 领域模型 -> 调用 service -> 响应"""
    input_ = ChatHistoryInput(
        id=req.id,
        session_id=req.session_id,
        bcn_group_id=req.bcn_group_id,
        to_bot=req.to_bot.to_domain(),
        limit=req.limit,
        before=req.before,
        after=req.after,
        timeout_ms=req.timeout_ms,
    )

    result = await service.handle_chat_history(input_)

    return ChatHistorySuccessResponse(
        ok=result.ok,
        session_id=result.session_id,
        messages=[BcnHistoryMessage.from_domain(msg) for msg in result.messages],
        has_more=result.has_more,
        next_before=result.next_before,
        next_after=result.next_after,
    )


# ─────────────────────────── 内部辅助 ───────────────────────────


def _build_sse_error(run_id: str, code: str, message: str, retryable: bool) -> str:
    """构造 SSE error 事件的文本帧"""
    event = SseEvent(
        event="error",
        data=json.dumps(
            {
                "run_id": run_id,
                "error": {"code": code, "message": message, "retryable": retryable},
            },
            ensure_ascii=False,
        ),
    )
    return event.to_sse()
