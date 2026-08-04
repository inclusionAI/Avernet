"""Device API Router — FastAPI endpoint definitions.

This module defines the HTTP endpoints for device management.
It only handles HTTP concerns: path/query/body parsing and error mapping.

Business logic is delegated to DeviceService from core layer.

According to README.md:
- Only HTTP protocol concerns
- Call core/<module>/services/ or dependencies/
- Request/Response types from sibling schemas.py
- Use HTTPException for errors
"""


import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator

# Auth dependencies (core layer)
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.access.admin_scopes import device_admin
from agentclaw.community.adapters.http.devices.converter import (
    connection_info_to_response,
    record_to_response,
    record_to_response_with_connection,
)
from agentclaw.community.adapters.http.devices.dependencies import get_operator_context
from agentclaw.community.adapters.http.devices.schemas import (
    ApiResponse,
    ApplyDeviceRequest,
    BatchSetDeviceEnvRequest,
    BatchSetDeviceEnvResult,
    BootstrapDeviceAuthRequest,
    ConfirmDeviceAliveRequest,
    DeviceBindingResponse,
    DeviceConnectionResponse,
    DeviceInstancesResponse,
    ExecShellRequest,
    ExecShellResult,
    ReleaseDeviceRequest,
    ReportDeviceStatusRequest,
    RestartDeviceRequest,
)
from agentclaw.community.api.device_service import DeviceServiceProtocol
from agentclaw.community.core.devices.models import OperatorContext
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


logger = get_logger()

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.post("", response_model=ApiResponse[DeviceBindingResponse])
async def apply_device(
    req: ApplyDeviceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[DeviceBindingResponse]:
    """Apply for a device.

    Allocates a new device for the authenticated user.
    """
    try:
        from agentclaw.community.core.devices.errors import (
            DeviceAllocateError,
            DeviceAlreadyExistsError,
            DeviceLimitExceededError,
            DeviceServiceError,
            ResourceInsufficientError,
        )

        operator = get_operator_context(user)
        # apply_device is a synchronous template method that performs blocking
        # I/O (Arca sandbox creation, up to a 2-minute timeout). Offload it to a
        # worker thread so it doesn't block the asyncio event loop.
        result = await asyncio.to_thread(
            service.apply_device,
            apply_reason=req.apply_reason,
            entity_id=req.entity_id or operator.staff_id,
            entity_type=req.entity_type.value if req.entity_type else "staff",
            operator=operator,
            bot_id=req.bot_id,
            engine=req.engine,
            symbol=None,  # Aligned with old code: router always passes None
        )

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=record_to_response(result),
        )
    except DeviceAlreadyExistsError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40902,
            data=None,
        )
    except DeviceLimitExceededError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40903,
            data=None,
        )
    except ResourceInsufficientError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=50301,
            data=None,
        )
    except DeviceAllocateError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=50001,
            data=None,
        )
    except DeviceServiceError as e:
        logger.warning(f"[apply_device] Error: {e}")
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=50000,
            data=None,
        )
    except Exception as e:
        logger.error(f"[apply_device] Unexpected error: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"设备申请失败: {str(e)}",
            error_code=50000,
            data=None,
        )


@router.post("/{binding_id:int}/release", response_model=ApiResponse[DeviceBindingResponse])
async def release_device(
    binding_id: int,
    req: ReleaseDeviceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[DeviceBindingResponse]:
    """Release a device.

    Marks the device binding as RELEASED.
    """
    try:
        from agentclaw.community.core.devices.errors import (
            DeviceNotFoundError,
            DeviceServiceError,
            InvalidDeviceStatusError,
        )

        operator = get_operator_context(user)
        result = service.release_device(
            binding_id=binding_id,
            release_reason=req.release_reason,
            reset=False,  # Simplified
            operator=operator,
        )

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=record_to_response(result),
        )
    except DeviceNotFoundError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40401,
            data=None,
        )
    except InvalidDeviceStatusError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40901,
            data=None,
        )
    except DeviceServiceError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=50000,
            data=None,
        )


@router.get("", response_model=ApiResponse[dict])
async def list_devices(
    entity_type: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[dict]:
    """List devices.

    Returns paginated list of device bindings for the authenticated user.
    """
    try:
        from agentclaw.community.utils import env_utils

        resolved_entity_id = user.staffId
        resolved_entity_type = entity_type or "staff"
        env = env_utils.get_current_env()

        total, items = service.list_devices(
            entity_id=resolved_entity_id,
            entity_type=resolved_entity_type,
            env=env,
            status=status,
            page=page,
            page_size=page_size,
        )

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data={
                "total": total,
                "items": [record_to_response(item) for item in items],
            },
        )
    except Exception as e:
        logger.error(f"[list_devices] Error: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"Failed to list devices: {str(e)}",
            error_code=50000,
            data=None,
        )


@router.get("/provider-inventory", response_model=ApiResponse[dict])
async def get_provider_inventory(
    entity_id: str | None = None,
    entity_type: str | None = None,
    env: str | None = None,
    status: str | None = None,
    page_size: int = 500,
    max_pages: int = 20,
    user: AuthenticatedUser = Depends(require_operator),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[dict]:
    """Provider inventory for rollout observation.

    Operator-only because omitting ``entity_id`` scans global device bindings
    within the bounded ``page_size``/``max_pages`` window.
    """
    try:
        result = service.get_provider_inventory(
            entity_id=entity_id,
            entity_type=entity_type,
            env=env,
            status=status,
            page_size=page_size,
            max_pages=max_pages,
        )
        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=result,
        )
    except Exception as e:
        logger.error(
            f"[get_provider_inventory] Error: user={user.staffId} error={e}",
            exc_info=True,
        )
        return ApiResponse(
            success=False,
            message=f"Failed to get provider inventory: {str(e)}",
            error_code=50000,
            data=None,
        )


@router.get("/{binding_id:int}", response_model=ApiResponse[DeviceBindingResponse])
async def get_device(
    binding_id: int,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[DeviceBindingResponse]:
    """Get device by binding ID."""
    try:
        from agentclaw.community.core.devices.errors import DeviceNotFoundError, DeviceServiceError

        result = service.get_device(binding_id=binding_id)

        if result.entity_id != user.staffId:
            return ApiResponse(
                success=False,
                message="无权访问该设备",
                error_code=403,
                data=None,
            )

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=record_to_response(result),
        )
    except DeviceNotFoundError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40402,
            data=None,
        )
    except DeviceServiceError as e:
        logger.warning(
            f"[get_device] device service error: binding_id={binding_id}: {e}"
        )
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=50000,
            data=None,
        )
    except Exception as e:
        logger.error(
            f"[get_device] unexpected error: binding_id={binding_id}: {e}",
            exc_info=True,
        )
        return ApiResponse(
            success=False,
            message="获取设备失败，请稍后重试",
            error_code=50000,
            data=None,
        )


@router.get("/by-id/{device_id}", response_model=ApiResponse[DeviceBindingResponse])
async def get_device_by_device_id(
    device_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[DeviceBindingResponse]:
    """Get device by device ID."""
    try:
        from agentclaw.community.core.devices.errors import DeviceNotFoundError

        result = service.get_device_by_device_id(device_id=device_id)

        if result.entity_id != user.staffId:
            return ApiResponse(
                success=False,
                message="无权访问该设备",
                error_code=403,
                data=None,
            )

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=record_to_response(result),
        )
    except DeviceNotFoundError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40405,
            data=None,
        )


@router.get("/{binding_id:int}/connection", response_model=ApiResponse[DeviceConnectionResponse])
async def get_device_connection(
    binding_id: int,
    port: int | None = None,
    ttl: int | None = None,
    device_uuid: str | None = Query(None, description="锁定多实例中的特定实例；不传则自动选活跃实例"),
    ws_conn_mode: str | None = Query(None, description="WebSocket connection mode: 'direct'(default)/'relay'"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[DeviceConnectionResponse]:
    """Get device connection info.

    Returns connection details for connecting to the device. ``device_uuid``
    (optional) targets a specific instance for multi-instance service bots; when
    the instance can't be resolved the provider raises instead of silently falling
    back to another instance.
    """
    try:
        from agentclaw.community.core.devices.errors import (
            DeviceNotFoundError,
            DeviceServiceError,
            InvalidDeviceStatusError,
        )

        operator = get_operator_context(user)
        result = service.get_device_connection(
            binding_id=binding_id,
            operator=operator,
            port=port,
            ttl=ttl,
            device_uuid=device_uuid,
            ws_conn_mode=ws_conn_mode,
        )

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=connection_info_to_response(result),
        )
    except DeviceNotFoundError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40403,
            data=None,
        )
    except InvalidDeviceStatusError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40301,
            data=None,
        )
    except ValueError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40001,
            data=None,
        )
    except DeviceServiceError as e:
        # BaaS/provider failure (e.g. BaasDeviceServiceError). The most common
        # case is the device not yet being ready — BaaS replies 503
        # NO_ACTIVE_DEVICES while the container/process is still coming up. That
        # is an expected, self-healing state, NOT a server fault: returning it
        # as a framework 500 gives the frontend an empty body it can't act on
        # and pollutes the 5xx error-rate alarms. Map it to a friendly business
        # response instead so the UI can show "设备未就绪，请稍候".
        detail = str(e)
        if "NO_ACTIVE_DEVICES" in detail:
            logger.info(
                f"[get_device_connection] device not ready (NO_ACTIVE_DEVICES): "
                f"binding_id={binding_id}"
            )
            return ApiResponse(
                success=False,
                message="设备未就绪，请重启 teamclaw 应用或切换其他 bot",
                error_code=40303,
                data=None,
            )
        logger.warning(
            f"[get_device_connection] device service error: "
            f"binding_id={binding_id}: {detail}"
        )
        return ApiResponse(
            success=False,
            message="获取设备连接失败，请稍后重试",
            error_code=50000,
            data=None,
        )


@router.get(
    "/bots/{bot_id}/connection",
    response_model=ApiResponse[DeviceConnectionResponse],
)
async def get_device_connection_by_bot(
    bot_id: str,
    port: int | None = None,
    device_uuid: str | None = Query(None, description="锁定多实例中的特定实例；不传则自动选活跃实例"),
    ws_conn_mode: str | None = Query(None, description="WebSocket connection mode: 'direct'(default)/'relay'"),
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[DeviceConnectionResponse]:
    """Get device connection info by bot_id (chat page main entry; §3).

    Resolves the runtime binding from bot_id via the success publish record
    (``ext.binding.online``), then reuses the binding_id connection logic.
    ``device_uuid`` (optional) targets a specific instance for multi-instance
    service bots; when it can't be resolved the provider raises instead of
    silently falling back to another instance.
    """
    try:
        from agentclaw.community.core.devices.errors import (
            DeviceNotFoundError,
            DeviceServiceError,
            InvalidDeviceStatusError,
        )

        operator = get_operator_context(user)
        result = service.get_device_connection_by_bot(
            bot_id=bot_id,
            operator=operator,
            port=port,
            device_uuid=device_uuid,
            ws_conn_mode=ws_conn_mode,
        )

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=connection_info_to_response(result),
        )
    except RuntimeError as e:
        # BotPublishNotFoundError / BindingNotFoundError → entry resolution failed.
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40403,
            data=None,
        )
    except DeviceNotFoundError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40403,
            data=None,
        )
    except InvalidDeviceStatusError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40301,
            data=None,
        )
    except ValueError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40001,
            data=None,
        )
    except DeviceServiceError as e:
        # Mirror the binding_id entry: NO_ACTIVE_DEVICES is an expected
        # not-ready state, mapped to a friendly business response instead of a
        # framework 5xx (see get_device_connection for rationale).
        detail = str(e)
        if "NO_ACTIVE_DEVICES" in detail:
            logger.info(
                f"[get_device_connection_by_bot] device not ready "
                f"(NO_ACTIVE_DEVICES): bot_id={bot_id}"
            )
            return ApiResponse(
                success=False,
                message="设备未就绪，请重启 teamclaw 应用或切换其他 bot",
                error_code=40303,
                data=None,
            )
        logger.warning(
            f"[get_device_connection_by_bot] device service error: "
            f"bot_id={bot_id}: {detail}"
        )
        return ApiResponse(
            success=False,
            message="获取设备连接失败，请稍后重试",
            error_code=50000,
            data=None,
        )


@router.get("/connectable", response_model=ApiResponse[dict])
async def list_connectable_devices(
    entity_id: str,
    entity_type: str,
    page: int = 1,
    page_size: int = 20,
    with_connection: bool = False,
    port: int | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[dict]:
    """List connectable devices."""
    # 越权校验：只允许查询自己的设备
    if entity_id != user.staffId:
        logger.warning(f"[list_connectable_devices] 权限拒绝: user={user.staffId} 尝试查询 entity_id={entity_id} 的设备信息")
        return ApiResponse(
            success=False,
            message="无权访问该用户的设备信息",
            error_code=403,
            data=None,
        )
    try:
        from agentclaw.community.core.devices.models import DeviceBindingInfo
        from agentclaw.community.utils import env_utils

        env = env_utils.get_current_env()
        operator = get_operator_context(user)

        total, items = service.list_connectable_devices(
            entity_id=entity_id,
            entity_type=entity_type,
            env=env,
            page=page,
            page_size=page_size,
            with_connection=with_connection,
            port=port,
            operator=operator,
        )

        # items 是 DeviceBindingInfo 列表，转为 API Response
        serialized_items = []
        for item in items:
            if isinstance(item, DeviceBindingInfo):
                serialized_items.append(
                    record_to_response_with_connection(item.record, item.connection).model_dump()
                )
            else:
                serialized_items.append(record_to_response(item).model_dump())

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data={
                "total": total,
                "items": serialized_items,
            },
        )
    except Exception as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=50000,
            data=None,
        )


@router.get("/connectable_admin", response_model=ApiResponse[dict])
async def list_connectable_devices_admin(
    page: int = 1,
    page_size: int = 20,
    with_connection: bool = False,
    port: int | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[dict]:
    """List all connectable devices (admin only)."""
    # Admin check
    if user.staffId not in device_admin():
        return ApiResponse(
            success=False,
            message="无权限访问",
            error_code=500,
            data=None,
        )

    try:
        from agentclaw.community.core.devices.models import DeviceBindingInfo
        from agentclaw.community.utils import env_utils

        env = env_utils.get_current_env()
        operator = get_operator_context(user)

        total, items = service.list_connectable_devices(
            entity_id=None,
            entity_type=None,
            env=env,
            page=page,
            page_size=page_size,
            with_connection=with_connection,
            port=port,
            operator=operator,
        )

        # items 是 DeviceBindingInfo 列表，转为 API Response
        serialized_items = []
        for item in items:
            if isinstance(item, DeviceBindingInfo):
                serialized_items.append(
                    record_to_response_with_connection(item.record, item.connection).model_dump()
                )
            else:
                serialized_items.append(record_to_response(item).model_dump())

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data={
                "total": total,
                "items": serialized_items,
            },
        )
    except Exception as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=50000,
            data=None,
        )


@router.post("/callback/alive", response_model=ApiResponse[DeviceBindingResponse])
async def report_device_alive(
    req: ConfirmDeviceAliveRequest,
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
    authorization: str = Header(..., description="Bearer token"),
) -> ApiResponse[DeviceBindingResponse]:
    """Device alive callback.

    Called by device when it starts up and becomes ready.
    When device transitions to ACTIVE, DeviceServiceRouter handles MCP sync
    in a background thread so the HTTP response is not blocked.
    """
    try:
        from agentclaw.community.core.devices.errors import (
            DeviceNotFoundError,
            InvalidDeviceStatusError,
        )

        # Parse Bearer token
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Invalid authorization header format. Expected 'Bearer <token>'",
            )
        token = authorization[7:]

        result = service.report_device_alive(
            device_id=req.device_id,
            token=token,
        )

        # service 返回 DeviceBindingRecord，统一转换
        data = record_to_response(result)

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=data,
        )
    except DeviceNotFoundError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40404,
            data=None,
        )
    except InvalidDeviceStatusError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40302,
            data=None,
        )


@router.post("/callback/status", response_model=ApiResponse[DeviceBindingResponse])
async def report_device_status(
    req: ReportDeviceStatusRequest,
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
    authorization: str = Header(..., description="Bearer token"),
) -> ApiResponse[DeviceBindingResponse]:
    """Device status callback.

    Called by device during startup to report status.
    """
    try:
        from agentclaw.community.core.devices.errors import (
            DeviceNotFoundError,
            InvalidDeviceStatusError,
        )

        # Parse Bearer token
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Invalid authorization header format. Expected 'Bearer <token>'",
            )
        token = authorization[7:]

        result = service.report_device_status(
            device_id=req.device_id,
            status=req.status,
            message=req.message,
            token=token,
        )

        # service 返回 DeviceBindingRecord，统一转换
        data = record_to_response(result)

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=data,
        )
    except DeviceNotFoundError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40404,
            data=None,
        )
    except InvalidDeviceStatusError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40302,
            data=None,
        )


@router.post("/callback/bootstrap-auth", response_model=ApiResponse[dict])
async def bootstrap_device_auth(
    req: BootstrapDeviceAuthRequest,
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[dict]:
    """Device bootstrap: sync ephemeral credentials and return agent_code.

    Called by device after startup to fetch passport token headers and agent_code.
    """
    try:
        from agentclaw.community.core.devices.errors import (
            DeviceNotFoundError,
            DeviceServiceError,
        )

        logger.info(
            f"[api.bootstrap_device_auth] Request: device_id={req.device_id}, "
            f"bot_id={req.bot_id}, owner_id={req.owner_id}"
        )

        result = service.bootstrap_device_auth(
            device_id=req.device_id,
            bot_id=req.bot_id,
            owner_id=req.owner_id,
        )

        return ApiResponse(
            success=True,
            message="OK",
            error_code=200,
            data=result,
        )
    except DeviceNotFoundError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40404,
            data=None,
        )
    except DeviceServiceError as e:
        logger.error(f"[bootstrap_device_auth] DeviceServiceError: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=500,
            data=None,
        )
    except Exception as e:
        logger.error(f"[bootstrap_device_auth] Unexpected error: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"设备启动认证失败: {str(e)}",
            error_code=500,
            data=None,
        )


@router.post("/exec_shell", response_model=ApiResponse[ExecShellResult])
async def exec_shell(
    req: ExecShellRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[ExecShellResult]:
    """Execute shell command on devices.

    Admin only endpoint.
    """
    # Admin check
    if user.staffId not in device_admin():
        return ApiResponse(
            success=False,
            message="无权限访问",
            error_code=500,
            data=None,
        )

    results = []
    successful = 0
    failed = 0

    for client_id in req.client_ids:
        try:
            result = service.exec_shell(
                device_id=client_id,
                shell_cmd=req.shell_cmd,
            )
            results.append({
                "client_id": client_id,
                "status": "success",
                "result": result,
            })
            successful += 1
        except Exception as e:
            results.append({
                "client_id": client_id,
                "status": "failed",
                "error": str(e),
            })
            failed += 1

    return ApiResponse(
        success=True,
        message=f"执行完成：{successful} 成功，{failed} 失败",
        error_code=200,
        data=ExecShellResult(
            total=len(req.client_ids),
            successful=successful,
            failed=failed,
            results=results,
        ),
    )


@router.post("/batch/env", response_model=ApiResponse[BatchSetDeviceEnvResult])
async def batch_set_device_env(
    req: BatchSetDeviceEnvRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[BatchSetDeviceEnvResult]:
    """Batch update device environment (admin only)."""
    if not req.binding_ids:
        return ApiResponse(success=False, message="binding_ids 不能为空", error_code=40001, data=None)

    try:
        from agentclaw.community.adapters.http.devices.schemas import Env

        # Validate env value
        env = Env.from_string(req.env)

        count, updated_ids = service.batch_set_env(
            binding_ids=req.binding_ids,
            env=env.value,
        )

        return ApiResponse(
            success=True,
            message=f"成功更新 {count}/{len(req.binding_ids)} 条记录的环境为 {env.value}",
            error_code=200,
            data=BatchSetDeviceEnvResult(
                total=len(req.binding_ids),
                updated=count,
                updated_ids=updated_ids,
            ),
        )
    except ValueError as e:
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40001,
            data=None,
        )


# =============================================================================
# Multi-instance — instance list (§1 frontend-api-contract)
# =============================================================================

@router.get(
    "/bots/{bot_id}/instances",
    response_model=ApiResponse[DeviceInstancesResponse],
)
async def get_instances_by_bot(
    bot_id: str,
    health_check: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[DeviceInstancesResponse]:
    """List device instances for a bot (chat page dropdown; bot_id entry).

    Resolves the runtime binding from bot_id via the success publish
    record (``ext.binding.online``), then queries BaaS for the device list.
    """
    try:
        result = service.get_instances_by_bot(
            bot_id=bot_id,
            health_check=health_check,
        )
        return ApiResponse(
            success=True,
            message="success",
            error_code=200,
            data=DeviceInstancesResponse(**result),
        )
    except RuntimeError as e:
        # BotPublishNotFoundError / BindingNotFoundError → entry resolution failed
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40403,
            data=None,
        )
    except Exception as e:
        logger.error(
            f"[get_instances_by_bot] Error: bot_id={bot_id}: {e}",
            exc_info=True,
        )
        return ApiResponse(
            success=False,
            message=f"获取实例列表失败: {str(e)}",
            error_code=50000,
            data=None,
        )


@router.get(
    "/{binding_id:int}/instances",
    response_model=ApiResponse[DeviceInstancesResponse],
)
async def get_instances(
    binding_id: int,
    health_check: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[DeviceInstancesResponse]:
    """List device instances by binding_id (admin/management; restart-progress).

    Validates the binding (baas / active / same env), then queries BaaS for
    the device list with health four-state synthesized per instance.
    """
    try:
        result = service.get_instances(
            binding_id=binding_id,
            health_check=health_check,
        )
        return ApiResponse(
            success=True,
            message="success",
            error_code=200,
            data=DeviceInstancesResponse(**result),
        )
    except RuntimeError as e:
        # BindingNotFoundError → entry resolution failed
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40403,
            data=None,
        )
    except Exception as e:
        logger.error(
            f"[get_instances] Error: binding_id={binding_id}: {e}",
            exc_info=True,
        )
        return ApiResponse(
            success=False,
            message=f"获取实例列表失败: {str(e)}",
            error_code=50000,
            data=None,
        )


@router.post("/{binding_id:int}/restart", response_model=ApiResponse[dict])
async def restart_device(
    binding_id: int,
    body: RestartDeviceRequest,
    operator: OperatorContext = Depends(get_operator_context),
    service: DeviceServiceProtocol = Injected(DeviceServiceProtocol),
) -> ApiResponse[dict]:
    """Restart a specific device instance (owner/admin only; §2).

    Body: ``{"device_uuid": "<uuid>"}``
    Returns: ``{"publish_id": <int>}``
    """
    from agentclaw.community.core.devices.errors import InvalidDeviceStatusError
    from agentclaw.community.core.devices.services.device_service_router import (
        BindingNotFoundError,
    )

    try:
        result = service.restart_device(
            binding_id=binding_id,
            device_uuid=body.device_uuid,
            operator=operator,
        )
        return ApiResponse(
            success=True,
            message="success",
            error_code=200,
            data=result,
        )
    except InvalidDeviceStatusError as e:
        # Non-owner restart → forbidden.
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40301,
            data=None,
        )
    except BindingNotFoundError as e:
        # binding invalid / not baas / cross-env / released.
        return ApiResponse(
            success=False,
            message=str(e),
            error_code=40403,
            data=None,
        )
    except Exception as e:
        # BaaS error propagation (BaasServiceError carries the raw response
        # text): PUBLISH_CONFLICT(409) / DEVICE_NOT_FOUND(404) / BOT_NOT_FOUND(404).
        error_msg = str(e)
        if "PUBLISH_CONFLICT" in error_msg:
            return ApiResponse(
                success=False,
                message="已有进行中发布，请稍后重试",
                error_code=40901,
                data=None,
            )
        if "DEVICE_NOT_FOUND" in error_msg:
            return ApiResponse(
                success=False,
                message="实例不存在或不归属该Bot",
                error_code=40404,
                data=None,
            )
        if "BOT_NOT_FOUND" in error_msg:
            return ApiResponse(
                success=False,
                message="Bot不存在",
                error_code=40401,
                data=None,
            )
        logger.error(
            f"[restart_device] Error: binding_id={binding_id}, "
            f"device_uuid={body.device_uuid}: {e}",
            exc_info=True,
        )
        return ApiResponse(
            success=False,
            message=f"重启设备失败: {error_msg}",
            error_code=50000,
            data=None,
        )
