"""Bot Health Checker REST API 路由。

端点：
- GET /api/v1/bot-health-checker/active_bots - 获取所有活跃 Bot 设备列表（分页）
- GET /api/v1/bot-health-checker/devices - 获取指定 Bot 的 PaaS 设备列表
- POST /api/v1/bot-health-checker/ttl/extend - 延长 Bot 设备 TTL
- POST /api/v1/bot-health-checker/health - 执行 Bot 设备健康检查
"""

from typing import Annotated, Any

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from secbaas.adapters.web.routers.open_api.dependencies import validate_api_key
from secbaas.api import ApiResponse
from secbaas.api.api_gateway import APIKeyRecord
from secbaas.api.health_check.bot import (
    BotHealthCheckerError,
    BotHealthCheckerService,
    SandboxNotFoundError,
    UnsupportedDeviceProviderError,
)
from secbaas.bootstrap import ApplicationContainer, Provide
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bot-health-checker", tags=["Bot健康检查"])

# 允许访问 Bot Health Checker 接口的 app_type
BOT_HEALTH_ALLOWED_APP_TYPES = {"health-checker"}


async def validate_bot_health_api_key(
    api_key_record: APIKeyRecord = Depends(validate_api_key),
) -> APIKeyRecord:
    """验证 API Key 并检查 app_type 是否允许访问 Bot Health Checker 接口。

    Args:
        api_key_record: 已验证的 API Key 记录

    Returns:
        APIKeyRecord: 验证通过的 API Key 记录

    Raises:
        HTTPException: 403 如果 app_type 不允许访问
    """
    if api_key_record.app_type not in BOT_HEALTH_ALLOWED_APP_TYPES:
        logger.warning(
            f"[validate_bot_health_api_key] API Key app_type not allowed: "
            f"app_id={api_key_record.app_id}, app_type={api_key_record.app_type}"
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "FORBIDDEN",
                "message": f"API Key app_type must be one of {BOT_HEALTH_ALLOWED_APP_TYPES}",
            },
        )
    return api_key_record


# ============ Request/Response Models ============


class ActiveBotsResponse(BaseModel):
    """活跃 Bot 列表响应"""

    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页")
    page_size: int = Field(..., description="每页数量")
    items: list[dict[str, Any]] = Field(default_factory=list, description="Bot 列表")


class ExtendTTLRequest(BaseModel):
    """延长 TTL 请求"""

    bot_id: Annotated[str, Field(..., description="Bot ID", min_length=1)]
    entity_id: Annotated[str, Field(..., description="实体 ID", min_length=1)]
    env: Annotated[str, Field(default="prod", description="环境参数")] = "prod"


VALID_STATUSES = {"draft", "validating", "online"}


def _parse_statuses(statuses: str | None) -> list[str]:
    """解析 statuses 参数。

    - None (未传) → ["online"]
    - 空字符串 → ["draft", "validating", "online"]（查全部）
    - 逗号分隔 → 拆分并校验
    """
    if statuses is None:
        return ["online"]
    if statuses.strip() == "":
        return ["draft", "validating", "online"]
    return [s.strip() for s in statuses.split(",")]


def _parse_statuses_from_list(statuses: list[str] | None) -> list[str]:
    """解析请求体中的 statuses 列表。

    - None → ["online"]
    - [] (空数组) → ["draft", "validating", "online"]（查全部）
    """
    if statuses is None:
        return ["online"]
    if len(statuses) == 0:
        return ["draft", "validating", "online"]
    return statuses


# ============ Error Codes ============


class ErrorCode:
    """错误码"""

    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED_PROVIDER = "UNSUPPORTED_PROVIDER"
    SANDBOX_NOT_FOUND = "SANDBOX_NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def raise_not_found(error_code: str, message: str) -> None:
    """抛出 404 错误"""
    raise HTTPException(
        status_code=404,
        detail={"error_code": error_code, "message": message},
    )


def raise_bad_request(error_code: str, message: str) -> None:
    """抛出 400 错误"""
    raise HTTPException(
        status_code=400,
        detail={"error_code": error_code, "message": message},
    )


def raise_internal_error(error_code: str, message: str) -> None:
    """抛出 500 错误"""
    raise HTTPException(
        status_code=500,
        detail={"error_code": error_code, "message": message},
    )


# ============ Endpoints ============


@router.get(
    "/active_bots",
    summary="获取所有活跃 Bot 设备列表（分页）",
    description="分页获取指定类型的所有活跃 Bot 设备信息",
    response_model=ApiResponse,
)
@inject
async def list_all_active_bot_device(
    page: Annotated[int, Query(ge=1, description="页码（从 1 开始）")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页大小")] = 20,
    bot_type: Annotated[
        str | None, Query(description="Bot 类型过滤 (personal/service)，不传则查询全部")
    ] = None,
    env: Annotated[str, Query(description="环境参数，默认 prod")] = "prod",
    service: BotHealthCheckerService = Depends(
        Provide[ApplicationContainer.services.bot_health_checker_service]
    ),
    api_key_record: APIKeyRecord = Depends(validate_bot_health_api_key),
) -> ApiResponse[Any]:
    """获取所有活跃 Bot 设备列表（分页）"""
    logger.info(
        f"[list_all_active_bot_device] page={page}, page_size={page_size}, bot_type={bot_type}, env={env}"
    )

    if bot_type is not None and bot_type not in ("personal", "service"):
        raise_bad_request(ErrorCode.INVALID_REQUEST, f"Invalid bot_type: {bot_type}")

    if not env or not env.strip():
        raise_bad_request(ErrorCode.INVALID_REQUEST, "env must not be empty")

    try:
        total, devices = await service.list_all_active_bot_device(
            page=page,
            page_size=page_size,
            bot_type=bot_type,
            env=env,
        )
        return ApiResponse(
            data={
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [d.model_dump() for d in devices],
            }
        )

    except UnsupportedDeviceProviderError as e:
        logger.error(f"[list_all_active_bot_device] Unsupported provider: {e}")
        raise_bad_request(ErrorCode.UNSUPPORTED_PROVIDER, str(e))
    except BotHealthCheckerError as e:
        logger.error(f"[list_all_active_bot_device] 查询失败: {e}", exc_info=True)
        raise_internal_error(ErrorCode.INTERNAL_ERROR, f"查询失败: {str(e)}")


@router.get(
    "/devices",
    summary="获取指定 Bot 的 PaaS 设备列表",
    description="获取指定 Bot 的所有 PaaS 设备信息（包含 TTL）",
    response_model=ApiResponse,
)
@inject
async def list_paas_device_by_bot(
    bot_id: Annotated[str, Query(description="Bot ID", min_length=1)],
    entity_id: Annotated[str, Query(description="实体 ID", min_length=1)],
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
    api_key_record: APIKeyRecord = Depends(validate_bot_health_api_key),
) -> ApiResponse[Any]:
    """获取指定 Bot 的 PaaS 设备列表"""
    status_list = _parse_statuses(statuses)

    logger.info(
        f"[list_paas_device_by_bot] bot_id={bot_id}, entity_id={entity_id}, statuses={status_list}, env={env}"
    )

    if not env or not env.strip():
        raise_bad_request(ErrorCode.INVALID_REQUEST, "env must not be empty")

    for s in status_list:
        if s not in VALID_STATUSES:
            raise_bad_request(ErrorCode.INVALID_REQUEST, f"Invalid status: {s}")

    try:
        result = await service.list_paas_device_by_bot(
            bot_id=bot_id,
            entity_id=entity_id,
            statuses=status_list,
            env=env,
        )
        return ApiResponse(data=result.model_dump())

    except SandboxNotFoundError as e:
        logger.error(f"[list_paas_device_by_bot] Sandbox not found: {e}")
        raise_not_found(ErrorCode.SANDBOX_NOT_FOUND, str(e))
    except UnsupportedDeviceProviderError as e:
        logger.error(f"[list_paas_device_by_bot] Unsupported provider: {e}")
        raise_bad_request(ErrorCode.UNSUPPORTED_PROVIDER, str(e))
    except BotHealthCheckerError as e:
        logger.error(f"[list_paas_device_by_bot] 查询失败: {e}", exc_info=True)
        raise_internal_error(ErrorCode.INTERNAL_ERROR, f"查询失败: {str(e)}")


@router.post(
    "/ttl/extend",
    summary="延长 Bot 设备 TTL",
    description="为指定 Bot 的所有设备延长 TTL（剩余 ≤ 16h 时延长到 +24h）",
    response_model=ApiResponse,
)
@inject
async def extend_ttl_by_bot(
    request: ExtendTTLRequest,
    service: BotHealthCheckerService = Depends(
        Provide[ApplicationContainer.services.bot_health_checker_service]
    ),
    api_key_record: APIKeyRecord = Depends(validate_bot_health_api_key),
) -> ApiResponse[Any]:
    """延长 Bot 设备 TTL"""
    logger.info(
        f"[extend_ttl_by_bot] bot_id={request.bot_id}, entity_id={request.entity_id}, "
        f"env={request.env}"
    )

    if not request.env or not request.env.strip():
        raise_bad_request(ErrorCode.INVALID_REQUEST, "env must not be empty")

    try:
        result = await service.extend_ttl_by_bot(
            bot_id=request.bot_id,
            entity_id=request.entity_id,
            env=request.env,
        )
        return ApiResponse(data=result.model_dump())

    except SandboxNotFoundError as e:
        logger.error(f"[extend_ttl_by_bot] Sandbox not found: {e}")
        raise_not_found(ErrorCode.SANDBOX_NOT_FOUND, str(e))
    except UnsupportedDeviceProviderError as e:
        logger.error(f"[extend_ttl_by_bot] Unsupported provider: {e}")
        raise_bad_request(ErrorCode.UNSUPPORTED_PROVIDER, str(e))
    except BotHealthCheckerError as e:
        logger.error(f"[extend_ttl_by_bot] TTL 延长失败: {e}", exc_info=True)
        raise_internal_error(ErrorCode.INTERNAL_ERROR, f"TTL 延长失败: {str(e)}")


@router.get(
    "/health",
    summary="执行 Bot 设备健康检查",
    description="检查指定 Bot 的所有设备健康状态（engine/adapter/gateway）",
    response_model=ApiResponse,
)
@inject
async def check_health_by_bot(
    bot_id: Annotated[str, Query(description="Bot ID", min_length=1)],
    entity_id: Annotated[str, Query(description="实体 ID", min_length=1)],
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
    api_key_record: APIKeyRecord = Depends(validate_bot_health_api_key),
) -> ApiResponse[Any]:
    """执行 Bot 设备健康检查"""
    status_list = _parse_statuses(statuses)

    logger.info(
        f"[check_health_by_bot] bot_id={bot_id}, entity_id={entity_id}, "
        f"statuses={status_list}, env={env}"
    )

    if not env or not env.strip():
        raise_bad_request(ErrorCode.INVALID_REQUEST, "env must not be empty")

    for s in status_list:
        if s not in VALID_STATUSES:
            raise_bad_request(ErrorCode.INVALID_REQUEST, f"Invalid status: {s}")

    try:
        result = await service.check_health_by_bot(
            bot_id=bot_id,
            entity_id=entity_id,
            statuses=status_list,
            env=env,
        )

        # Bot 维度日志
        logger.info(
            f"[check_health_by_bot] bot维度结果: "
            f"bot_id={result.bot_id}, entity_id={result.entity_id}, "
            f"bot_type={result.bot_type}, overall_healthy={result.overall_healthy}, "
            f"healthy_count={result.healthy_count}, unhealthy_count={result.unhealthy_count}"
        )
        # 沙箱维度日志
        for device in result.devices:
            failed_checkers = [
                name for name, r in device.checkers.items() if not r.healthy
            ]
            logger.info(
                f"[check_health_by_bot] 沙箱维度结果: "
                f"bot_id={result.bot_id}, entity_id={result.entity_id}, "
                f"bot_type={result.bot_type}, paas_device_id={device.paas_device_id}, "
                f"overall_healthy={device.overall_healthy}, "
                f"failed_checkers={failed_checkers}"
            )

        return ApiResponse(data=result.model_dump())

    except SandboxNotFoundError as e:
        logger.error(
            f"[check_health_by_bot] Sandbox not found: "
            f"bot_id={bot_id}, entity_id={entity_id}, error={e}"
        )
        raise_not_found(ErrorCode.SANDBOX_NOT_FOUND, str(e))
    except UnsupportedDeviceProviderError as e:
        logger.error(
            f"[check_health_by_bot] Unsupported provider: "
            f"bot_id={bot_id}, entity_id={entity_id}, error={e}"
        )
        raise_bad_request(ErrorCode.UNSUPPORTED_PROVIDER, str(e))
    except BotHealthCheckerError as e:
        logger.error(
            f"[check_health_by_bot] 健康检查失败: "
            f"bot_id={bot_id}, entity_id={entity_id}, error={e}",
            exc_info=True,
        )
        raise_internal_error(ErrorCode.INTERNAL_ERROR, f"健康检查失败: {str(e)}")


@router.get(
    "/alive",
    summary="检查 Bot 设备是否活跃",
    description="检查指定 Bot 的所有设备是否在指定分钟数内有活跃会话",
    response_model=ApiResponse,
)
@inject
async def check_alive_by_bot(
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
    api_key_record: APIKeyRecord = Depends(validate_bot_health_api_key),
) -> ApiResponse[Any]:
    """检查 Bot 设备是否活跃"""
    status_list = _parse_statuses(statuses)

    logger.info(
        f"[check_alive_by_bot] bot_id={bot_id}, entity_id={entity_id}, "
        f"minutes={minutes}, statuses={status_list}, env={env}"
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

        # Bot 维度日志
        logger.info(
            f"[check_alive_by_bot] bot维度结果: "
            f"bot_id={result.bot_id}, entity_id={result.entity_id}, "
            f"bot_type={result.bot_type}, overall_alive={result.overall_alive}, "
            f"live_count={result.live_count}, idle_count={result.idle_count}, "
            f"unknown_count={result.unknown_count}, error_count={result.error_count}"
        )
        # 沙箱维度日志
        for device in result.devices:
            logger.info(
                f"[check_alive_by_bot] 沙箱维度结果: "
                f"bot_id={result.bot_id}, entity_id={result.entity_id}, "
                f"bot_type={result.bot_type}, paas_device_id={device.paas_device_id}, "
                f"status={device.status}, "
                f"last_session_time={device.last_session_time}"
            )

        return ApiResponse(data=result.model_dump())

    except SandboxNotFoundError as e:
        logger.error(
            f"[check_alive_by_bot] Sandbox not found: "
            f"bot_id={bot_id}, entity_id={entity_id}, error={e}"
        )
        raise_not_found(ErrorCode.SANDBOX_NOT_FOUND, str(e))
    except UnsupportedDeviceProviderError as e:
        logger.error(
            f"[check_alive_by_bot] Unsupported provider: "
            f"bot_id={bot_id}, entity_id={entity_id}, error={e}"
        )
        raise_bad_request(ErrorCode.UNSUPPORTED_PROVIDER, str(e))
    except BotHealthCheckerError as e:
        logger.error(
            f"[check_alive_by_bot] 活跃检查失败: "
            f"bot_id={bot_id}, entity_id={entity_id}, error={e}",
            exc_info=True,
        )
        raise_internal_error(ErrorCode.INTERNAL_ERROR, f"活跃检查失败: {str(e)}")


@router.get(
    "/sandbox",
    summary="通过 sandbox_id 反查沙箱信息",
    description="根据 sandbox_id 查询沙箱信息，支持 ac_binding 和 baas_device 两张表",
    response_model=ApiResponse,
)
@inject
async def get_sandbox_info(
    sandbox_id: Annotated[str, Query(description="沙箱 ID", min_length=1)],
    service: BotHealthCheckerService = Depends(
        Provide[ApplicationContainer.services.bot_health_checker_service]
    ),
    api_key_record: APIKeyRecord = Depends(validate_bot_health_api_key),
) -> ApiResponse[Any]:
    """通过 sandbox_id 反查沙箱信息"""
    logger.info(f"[get_sandbox_info] sandbox_id={sandbox_id}")

    if not sandbox_id or not sandbox_id.strip():
        raise_bad_request(ErrorCode.INVALID_REQUEST, "sandbox_id must not be empty")

    try:
        result = await service.get_sandbox_info(
            sandbox_id=sandbox_id,
        )

        if result is None:
            raise_not_found(
                ErrorCode.SANDBOX_NOT_FOUND,
                f"Sandbox not found: sandbox_id={sandbox_id}",
            )

        return ApiResponse(data=result)

    except HTTPException:
        raise
    except BotHealthCheckerError as e:
        logger.error(f"[get_sandbox_info] 查询失败: {e}", exc_info=True)
        raise_internal_error(ErrorCode.INTERNAL_ERROR, f"查询失败: {str(e)}")
