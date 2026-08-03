"""Open API Run Router

Provides Open API endpoints for single-turn conversations.
"""

import time

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, status

from secbaas.community.adapters.web.routers.open_api.dependencies import (
    get_bot_chat_context,
    validate_api_key,
)
from secbaas.community.adapters.web.routers.open_api.model import (
    ExtraInfo,
    RunCancelResponse,
    RunCancelResponseData,
    RunRequest,
    RunResponse,
    RunResponseData,
    RunResultData,
    RunResultResponse,
    RunResultResponseData,
)
from secbaas.community.api.api_gateway import APIKeyRecord
from secbaas.community.api.bot_runtime import (
    BotBindingNotFoundError,
    BotChatContext,
    BotNotAvailableError,
    BotNotFoundError,
    BotRunner,
    BotRunStatusConflictError,
    BotServiceError,
    TooManyRequestsError,
)
from secbaas.community.api.open_api import OpenAPICode
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router-open-api")

router = APIRouter(prefix="/openapi/v1", tags=["runs"])


@router.post(
    "/runs",
    response_model=RunResponse,
    summary="Single-turn conversation",
    description="Invoke a Bot for a single-turn conversation using a Bearer Token-authenticated API Key",
    responses={
        200: {"description": "Conversation successful"},
        400: {"description": "Invalid parameters"},
        401: {"description": "Authentication failed"},
        404: {"description": "Bot not found"},
        503: {"description": "Bot unavailable"},
        500: {"description": "Internal server error"},
    },
)
@inject
async def run_chat(
    request: RunRequest,
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    context: BotChatContext = Depends(get_bot_chat_context),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> RunResponse:
    """Single-turn conversation endpoint

    Args:
        request: Conversation request
        api_key_record: Record from API Key validation, containing bot_id (app_id), api_key_prefix, etc.
        context: Request context (identity authentication, caller info, etc.)
        bot_runner: BotRunner instance

    Returns:
        RunResponse: Conversation response
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

        # 3. Build response
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
            detail={"code": 50001, "message": f"Internal server error: {str(e)}"},
        )


@router.get(
    "/runs/{run_id}",
    response_model=RunResultResponse,
    summary="Query conversation result",
    description="Query the execution result of a conversation task by run_id",
    responses={
        200: {"description": "Query successful"},
        401: {"description": "Authentication failed"},
        404: {"description": "Run not found"},
    },
)
@inject
async def get_run_result(
    run_id: str,
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> RunResultResponse:
    """Query conversation result endpoint

    Args:
        run_id: Run ID
        api_key_record: Record from API Key validation
        bot_runner: BotRunner instance

    Returns:
        RunResultResponse: Run result response
    """
    try:
        logger.info(
            f"get_run_result: run_id={run_id}, api_key_prefix={api_key_record.api_key_prefix}"
        )

        run_info = bot_runner.get_result(run_id)

        # Verify run ownership to prevent horizontal privilege escalation
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

        # Build result data
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


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunCancelResponse,
    summary="Cancel a conversation by run_id",
    description="Abort an in-progress Bot conversation identified by run_id. "
    "Only the API Key holder that owns the run may cancel it; runs in a "
    "terminal state (COMPLETED / FAILED / TIME_OUT / ABORTED) return 409.",
    responses={
        200: {"description": "Run aborted successfully"},
        401: {"description": "Authentication failed"},
        404: {"description": "Run not found or not owned by this API Key"},
        409: {"description": "Run is already in a terminal state"},
        503: {"description": "Bot / engine unavailable"},
        500: {"description": "Internal server error"},
    },
)
@inject
async def cancel_run(
    run_id: str,
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    bot_runner: BotRunner = Depends(Provide[ApplicationContainer.services.bot_runner]),
) -> RunCancelResponse:
    """Cancel (abort) an in-progress conversation by run_id

    Args:
        run_id: Run ID to abort
        api_key_record: Record from API Key validation
        bot_runner: BotRunner instance

    Returns:
        RunCancelResponse: Cancel response with aborted run info
    """
    try:
        logger.info(
            f"cancel_run: run_id={run_id}, bot_id={api_key_record.app_id}, "
            f"api_key_prefix={api_key_record.api_key_prefix}"
        )

        # 1. 取 run 记录并校验归属（不泄漏 run 存在性 → 一律 404）
        try:
            run_info = bot_runner.get_result(run_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": 40401, "message": f"Run not found: {run_id}"},
            )

        if (
            run_info.api_key_prefix != api_key_record.api_key_prefix
            or run_info.bot_id != api_key_record.app_id
        ):
            logger.warning(
                f"cancel_run ownership mismatch: run_id={run_id}, "
                f"expected api_key_prefix={api_key_record.api_key_prefix}/bot_id={api_key_record.app_id}, "
                f"actual api_key_prefix={run_info.api_key_prefix}/bot_id={run_info.bot_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": 40401, "message": f"Run not found: {run_id}"},
            )

        # 2. 中止 run（引擎 chat.abort + 落库 ABORTED）
        aborted_info = await bot_runner.cancel_run(run_id)

        logger.info(
            f"cancel_run success: run_id={run_id}, status={aborted_info.status}"
        )

        return RunCancelResponse(
            code=0,
            message="success",
            data=RunCancelResponseData(
                run_id=aborted_info.run_id,
                status=aborted_info.status,
                aborted_at=aborted_info.completed_at,
            ),
        )

    except HTTPException:
        raise
    except BotRunStatusConflictError as e:
        logger.warning(f"cancel_run status conflict: run_id={run_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": 40901, "message": str(e)},
        )
    except BotNotAvailableError as e:
        logger.warning(f"cancel_run bot not available: run_id={run_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": 60001, "message": str(e)},
        )
    except BotServiceError as e:
        logger.warning(f"cancel_run bot service error: run_id={run_id}, error={e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": OpenAPICode.BUSINESS_ERROR, "message": str(e)},
        )
    except Exception as e:
        logger.error(
            f"cancel_run unexpected error: run_id={run_id}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": 50001, "message": f"Internal server error: {str(e)}"},
        )
