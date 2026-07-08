"""Internal health-checker router — service-to-service endpoints without API Key auth.

These endpoints are intended for internal callers (e.g. backend agentclaw)
that authenticate via MOSN service mesh rather than application-level API keys.
They sit under the ``/internal`` prefix alongside
:mod:`secbaas.adapters.web.routers.internal_router`, following the same
convention: no ``validate_api_key`` dependency, security provided by network
isolation.

The external (API-Key-protected) equivalents live in
:mod:`secbaas.adapters.web.routers.bot_health_checker_router` under
``/api/v1/bot-health-checker``.
"""

from typing import Annotated, Any

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, Query

from secbaas.adapters.web.routers.health_checker.health_checker_router import (
    VALID_STATUSES,
    ErrorCode,
    _parse_statuses,
    raise_bad_request,
    raise_internal_error,
    raise_not_found,
)
from secbaas.api import ApiResponse
from secbaas.api.health_check.bot import (
    BotHealthCheckerError,
    BotHealthCheckerService,
    SandboxNotFoundError,
    UnsupportedDeviceProviderError,
)
from secbaas.bootstrap import ApplicationContainer, Provide
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/internal/bot-health-checker", tags=["internal"])


@router.get(
    "/alive",
    summary="[Internal] 检查 Bot 设备是否活跃（无 API Key 鉴权）",
    description=(
        "内部服务间调用端点，供 backend (agentclaw) 等内部服务通过 MOSN "
        "服务网格调用。与 /api/v1/bot-health-checker/alive 功能完全一致，"
        "但不要求 Authorization Bearer token。安全依赖 MOSN 网络隔离。"
    ),
    response_model=ApiResponse,
)
@inject
async def internal_check_alive_by_bot(
    bot_id: Annotated[str, Query(description="Bot ID", min_length=1)],
    entity_id: Annotated[str, Query(description="实体 ID", min_length=1)],
    minutes: Annotated[
        int, Query(description="检查最近 N 分钟内的活跃会话", ge=1)
    ] = 1440,
    statuses: Annotated[
        str | None,
        Query(
            description="查询状态列表，逗号分隔（仅 service 类型有效: draft/validating/online），默认 online，传空则查全部"
        ),
    ] = None,
    env: Annotated[str, Query(description="环境参数，默认 prod")] = "prod",
    service: BotHealthCheckerService = Depends(
        Provide[ApplicationContainer.services.bot_health_checker_service]
    ),
) -> ApiResponse[Any]:
    """内部服务间 Bot 活跃检查（无 API Key 鉴权）。

    与 /api/v1/bot-health-checker/alive 调用同一个
    BotHealthCheckerService.check_alive_by_bot，返回格式一致。
    """
    status_list = _parse_statuses(statuses)

    logger.info(
        "[internal_check_alive_by_bot] bot_id=%s, entity_id=%s, "
        "minutes=%s, statuses=%s, env=%s",
        bot_id,
        entity_id,
        minutes,
        status_list,
        env,
    )

    if not env or not env.strip():
        raise_bad_request(ErrorCode.INVALID_REQUEST, "env must not be empty")

    for s in status_list:
        if s not in VALID_STATUSES:
            raise_bad_request(ErrorCode.INVALID_REQUEST, f"Invalid status: {s}")

    try:
        result = await service.check_alive_by_bot(
            bot_id=bot_id,
            entity_id=entity_id,
            minutes=minutes,
            statuses=status_list,
            env=env,
        )

        logger.info(
            "[internal_check_alive_by_bot] bot维度结果: "
            "bot_id=%s, entity_id=%s, bot_type=%s, overall_alive=%s, "
            "live_count=%s, idle_count=%s, unknown_count=%s, error_count=%s",
            result.bot_id,
            result.entity_id,
            result.bot_type,
            result.overall_alive,
            result.live_count,
            result.idle_count,
            result.unknown_count,
            result.error_count,
        )
        for device in result.devices:
            logger.info(
                "[internal_check_alive_by_bot] 沙箱维度结果: "
                "bot_id=%s, entity_id=%s, bot_type=%s, "
                "paas_device_id=%s, status=%s, last_session_time=%s",
                result.bot_id,
                result.entity_id,
                result.bot_type,
                device.paas_device_id,
                device.status,
                device.last_session_time,
            )

        return ApiResponse(data=result.model_dump())

    except SandboxNotFoundError as e:
        logger.error(
            "[internal_check_alive_by_bot] Sandbox not found: "
            "bot_id=%s, entity_id=%s, error=%s",
            bot_id,
            entity_id,
            e,
        )
        raise_not_found(ErrorCode.SANDBOX_NOT_FOUND, str(e))
    except UnsupportedDeviceProviderError as e:
        logger.error(
            "[internal_check_alive_by_bot] Unsupported provider: "
            "bot_id=%s, entity_id=%s, error=%s",
            bot_id,
            entity_id,
            e,
        )
        raise_bad_request(ErrorCode.UNSUPPORTED_PROVIDER, str(e))
    except BotHealthCheckerError as e:
        logger.error(
            "[internal_check_alive_by_bot] 活跃检查失败: "
            "bot_id=%s, entity_id=%s, error=%s",
            bot_id,
            entity_id,
            e,
            exc_info=True,
        )
        raise_internal_error(ErrorCode.INTERNAL_ERROR, f"活跃检查失败: {str(e)}")
