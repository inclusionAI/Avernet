"""Open API Run 路由

提供单次对话的 Open API 端点。
"""

import time

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, status

from secbaas.adapters.web.routers.open_api.dependencies import (
    get_bot_chat_context,
    validate_api_key,
)
from secbaas.adapters.web.routers.open_api.model import (
    ExtraInfo,
    RunRequest,
    RunResponse,
    RunResponseData,
    RunResultData,
    RunResultResponse,
    RunResultResponseData,
)
from secbaas.api.api_gateway import APIKeyRecord
from secbaas.api.bot_runtime import (
    BotBindingNotFoundError,
    BotChatContext,
    BotNotAvailableError,
    BotNotFoundError,
    BotRunner,
    BotServiceError,
    TooManyRequestsError,
)
from secbaas.api.open_api import OpenAPICode
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

logger = get_logger("router-open-api")

router = APIRouter(prefix="/openapi/v1", tags=["Open API Run"])


@router.post(
    "/runs",
    response_model=RunResponse,
    summary="单次对话",
    description="通过 Bearer Token 认证的 API Key 调用 Bot 进行单次对话",
    responses={
        200: {"description": "对话成功"},
        400: {"description": "参数错误"},
        401: {"description": "认证失败"},
        404: {"description": "Bot 不存在"},
        503: {"description": "Bot 不可用"},
        500: {"description": "服务内部错误"},
    },
)
@inject
async def run_chat(
    request: RunRequest,
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> RunResponse:
    """单次对话端点

    Args:
        request: 对话请求
        api_key_record: 从 API Key 验证获取的记录，包含 bot_id (app_id)、api_key_prefix 等
        context: 请求上下文（身份认证、调用者信息等）
        bot_runner: BotRunner 实例

    Returns:
        RunResponse: 对话响应
    """
    try:
        metadata = request.metadata or {}

        logger.info(
            f"run_chat: bot_id={api_key_record.app_id}, "
            f"api_key_prefix={api_key_record.api_key_prefix}"
        )

        t_start = time.monotonic()
        run_id = await bot_runner.chat(
            bot_id=api_key_record.app_id,
            message=request.message,
            context=context,
            metadata=metadata,
        )
        elapsed_ms = (time.monotonic() - t_start) * 1000

        logger.info(
            f"run_chat success: bot_id={api_key_record.app_id}, run_id={run_id}, "
            f"elapsed={elapsed_ms:.1f}ms"
        )

        # 3. 构造响应
        return RunResponse(
            code=0, message="success", data=RunResponseData(run_id=run_id)
        )

    except BotBindingNotFoundError as e:
        logger.warning(
            f"run_chat binding not found: bot_id={api_key_record.app_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 60001, "message": str(e)},
        )
    except TooManyRequestsError as e:
        logger.warning(
            f"run_chat too many requests: bot_id={api_key_record.app_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": 42901, "message": str(e)},
        )
    except BotNotFoundError as e:
        logger.warning(
            f"run_chat bot not found: bot_id={api_key_record.app_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 60001, "message": str(e)},
        )
    except BotNotAvailableError as e:
        logger.warning(
            f"run_chat bot not available: bot_id={api_key_record.app_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": 60001, "message": str(e)},
        )
    except BotServiceError as e:
        logger.error(
            f"run_chat bot service error: bot_id={api_key_record.app_id}, error={e}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": OpenAPICode.BUSINESS_ERROR, "message": str(e)},
        )
    except Exception as e:
        logger.error(
            f"run_chat unexpected error: bot_id={api_key_record.app_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"服务内部错误: {str(e)}"},
        )


@router.get(
    "/runs/{run_id}",
    response_model=RunResultResponse,
    summary="查询对话结果",
    description="根据 run_id 查询对话任务的执行结果",
    responses={
        200: {"description": "查询成功"},
        401: {"description": "认证失败"},
        404: {"description": "Run 不存在"},
    },
)
@inject
async def get_run_result(
    run_id: str,
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> RunResultResponse:
    """查询对话结果端点

    Args:
        run_id: 运行 ID
        api_key_record: 从 API Key 验证获取的记录
        bot_runner: BotRunner 实例

    Returns:
        RunResultResponse: 运行结果响应
    """
    try:
        logger.info(
            f"get_run_result: run_id={run_id}, api_key_prefix={api_key_record.api_key_prefix}"
        )

        run_info = bot_runner.get_result(run_id)

        # 校验 run 归属，防止横向越权
        if (
            run_info.api_key_prefix != api_key_record.api_key_prefix
            or run_info.bot_id != api_key_record.app_id
        ):
            logger.warning(
                f"get_run_result ownership mismatch: run_id={run_id}, "
                f"expected api_key_prefix={api_key_record.api_key_prefix}/bot_id={api_key_record.app_id}, "
                f"actual api_key_prefix={run_info.api_key_prefix}/bot_id={run_info.bot_id}"
            )
            return RunResultResponse(
                code=OpenAPICode.BUSINESS_ERROR,
                message=f"Run not found: {run_id}",
                data=None,
            )

        # 构造结果数据
        result_data = None
        if run_info.result_content:
            extra = None
            if run_info.result_extra:
                extra = ExtraInfo(usage=run_info.result_extra.get("usage"))
            result_data = RunResultData(
                content=run_info.result_content,
                extra=extra,
            )

        if run_info.metadata and "session_id" in run_info.metadata:
            session_id = run_info.metadata.get("session_id")
        elif run_info.result_extra and "session_id" in run_info.result_extra:
            session_id = run_info.result_extra.get("session_id")
        else:
            session_id = ""

        logger.info(
            f"get_run_result success: run_id={run_id}, status={run_info.status}"
        )

        return RunResultResponse(
            code=0,
            message="success",
            data=RunResultResponseData(
                run_id=run_info.run_id,
                bot_id=run_info.bot_id,
                session_id=session_id,
                status=run_info.status,
                created_at=run_info.gmt_create,
                completed_at=run_info.completed_at,
                result=result_data,
                error=run_info.error,
            ),
        )

    except KeyError:
        logger.warning(f"get_run_result not found: run_id={run_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40401, "message": f"Run not found: {run_id}"},
        )
