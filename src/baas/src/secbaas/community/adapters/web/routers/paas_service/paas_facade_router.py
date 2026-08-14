"""PaaS Service Facade REST API routes.

Exposes PaasServiceFacade methods as HTTP endpoints for testing and external access.

Endpoints:
- POST /api/v1/paas/devices - Create device via PaasServiceFacade
- DELETE /api/v1/paas/devices/{paas_device_id} - Destroy device via PaasServiceFacade
- POST /api/v1/paas/devices/{paas_device_id}/commands - Execute command via PaasServiceFacade
- GET /api/v1/paas/devices/{paas_device_id}/ws-info - Get WebSocket connection info via PaasServiceFacade
- GET /api/v1/paas/devices/{paas_device_id}/info - Get device info via PaasServiceFacade
- PUT /api/v1/paas/devices/{paas_device_id}/outbound-rule - Update outbound operation rule via PaasServiceFacade
- GET/POST/PUT/DELETE /api/v1/paas/devices/{paas_device_id}/invoke-http/{port}/{path}
    - Proxy HTTP requests to internal device services
- POST /api/v1/paas/devices/{paas_device_id}/open-folder - Open folder in device file explorer
- PUT /api/v1/paas/devices/{paas_device_id}/ttl - Update device TTL
"""

import base64
import binascii
from datetime import datetime
from typing import Annotated, Any, cast

from dependency_injector.wiring import Provide, inject
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse, SuccessResponse
from secbaas.community.api.device_manage import (
    ArcaCreationResult,
    ArcaDeviceConfig,
    CommandResult,
    DeviceFacadeException,
    DeviceInfo,
    DeviceNotActiveException,
    DeviceNotFoundException,
    ErrorCode,
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
    PaasServiceFacade,
    SigmaCreationResult,
    SigmaDeviceConfig,
)
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/paas", tags=["PaaS设备管理(测试)"])


# ============================================================================
# Error Code to HTTP Status Mapping
# ============================================================================

ERROR_CODE_TO_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.DEVICE_NOT_FOUND: 404,
    ErrorCode.DEVICE_ALREADY_EXISTS: 409,
    ErrorCode.DEVICE_CREATION_FAILED: 500,
    ErrorCode.DEVICE_DESTROY_FAILED: 500,
    ErrorCode.DEVICE_NOT_READY: 503,
    ErrorCode.COMMAND_TIMEOUT: 504,
    ErrorCode.COMMAND_FAILED: 500,
    ErrorCode.DEVICE_UNAVAILABLE: 503,
    ErrorCode.PLATFORM_UNAVAILABLE: 503,
    ErrorCode.AUTH_FAILED: 401,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.CONFIG_INVALID: 400,
}


# ============================================================================
# Hop-by-Hop Headers Filtering
# ============================================================================

# Headers that should not be forwarded to internal services
# as they control connection behavior between intermediaries
HOP_BY_HOP_HEADERS: set[str] = {
    "connection",
    "keep-alive",
    "proxy-authorization",
    "proxy-authenticate",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
    "host",
}


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filter out hop-by-hop headers from request/response headers.

    Args:
        headers: Original headers dict.

    Returns:
        Filtered headers dict with hop-by-hop headers removed.
    """
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


# ============================================================================
# Pydantic Models
# ============================================================================


class CreateDeviceRequest(BaseModel):
    """Request model for creating a device.

    Uses tenant_name + device_template_uuid + detail_config pattern per D-03.
    Platform type is derived from resolved template, not specified directly.
    """

    tenant_name: str = Field(
        ...,  # Required per D-04
        min_length=1,
        description="租户名称，用于业务层隔离和设备创建授权",
    )
    device_template_uuid: str | None = Field(
        default=None,
        description="设备模板UUID (可选，不提供则使用租户默认模板)",
    )
    detail_config: ArcaDeviceConfig | SigmaDeviceConfig | None = Field(
        default=None,
        description="详细配置 (可选，会覆盖模板配置中的相同字段)",
    )


class ExecuteCommandRequest(BaseModel):
    """Request model for executing a command on a device."""

    cmd: str = Field(..., min_length=1, description="要执行的命令")
    env: dict[str, str] | None = Field(default=None, description="环境变量（可选）")


class WsConnectionInfoResponse(BaseModel):
    """WebSocket connection information response model.

    Returned by the ws-info endpoint. Callers use ws_url and token to
    establish direct WebSocket connections via agentclawproxy gateway.
    """

    ws_url: str = Field(..., description="Full WebSocket URL")
    token: str = Field(..., description="Proxypass JWT token for authentication")
    target: str = Field(..., description="Target identifier (ARCA_sandboxId:port)")
    expires_at: datetime = Field(..., description="Token expiration timestamp (UTC)")


class WsConnectionErrorResponse(BaseModel):
    """Error response for ws-info endpoint."""

    error: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    paas_device_id: str | None = Field(None, description="Device ID if applicable")


class PaasErrorResponse(BaseModel):
    """Error response model for PaaS facade operations."""

    error_code: str = Field(..., description="错误代码")
    message: str = Field(..., description="错误消息")
    context: dict[str, Any] | None = Field(default=None, description="额外上下文信息")


class DeviceInfoResponse(BaseModel):
    """Device info response model.

    Returned by the device info endpoint. Contains device details
    from the underlying PaaS platform.
    """

    platform: str = Field(..., description="平台类型: arca|sigma|mock")
    status: str = Field(..., description="设备状态")
    sandbox_id: str | None = Field(default=None, description="沙箱ID (Arca平台)")
    template_id: str | None = Field(default=None, description="模板ID")
    ip_address: str | None = Field(default=None, description="内网IP")
    ttl_seconds: int | None = Field(default=None, description="剩余存活时间(秒)")
    created_at: datetime | None = Field(default=None, description="创建时间")
    name: str | None = Field(default=None, description="沙箱名称")
    description: str | None = Field(default=None, description="描述")


class TTLInfoResponse(BaseModel):
    """TTL update response model.

    Returned by the TTL update endpoint. Contains old and new expiration times.
    """

    paas_device_id: str = Field(..., description="设备ID")
    old_expiration_time: datetime | None = Field(default=None, description="原过期时间")
    new_expiration_time: datetime | None = Field(default=None, description="新过期时间")
    success: bool = Field(..., description="是否成功")
    skipped: bool = Field(default=False, description="是否跳过(未过期)")
    error: str | None = Field(default=None, description="错误信息(如有)")


class OpenFolderRequest(BaseModel):
    """Request model for opening a folder in device file explorer.

    Only supported on LOCAL platform. Non-LOCAL platforms return 501.
    """

    folder_path: str | None = Field(
        default=None, description="要打开的文件夹路径（可选，不传则使用平台默认路径）"
    )


# ============================================================================
# Error Handling
# ============================================================================


def _handle_facade_exception(exc: DeviceFacadeException) -> HTTPException:
    """Convert DeviceFacadeException to HTTPException with proper status code.

    Args:
        exc: The DeviceFacadeException to convert

    Returns:
        HTTPException with appropriate status code and error detail
    """
    error_code = exc.original_error.code
    status_code = ERROR_CODE_TO_HTTP_STATUS.get(error_code, 500)

    detail = {
        "error_code": error_code.value,
        "message": exc.original_error.message,
        "context": {
            "operation": exc.operation,
            "platform_type": exc.platform_type,
            "template_id": exc.template_id,
            "paas_device_id": exc.paas_device_id,
        },
    }

    return HTTPException(status_code=status_code, detail=detail)


# ============================================================================
# Endpoints
# ============================================================================


@router.post(
    "/devices",
    response_model=ApiResponse[ArcaCreationResult | SigmaCreationResult],
)
@inject
async def create_device(
    request: CreateDeviceRequest,
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> ApiResponse[ArcaCreationResult | SigmaCreationResult]:
    """Create a device via PaasServiceFacade.

    Args:
        request: Device creation request with tenant name, optional template UUID,
                 and optional detail config for template overrides

    Returns:
        DeviceCreationResult with created device details

    Raises:
        HTTPException: On creation failure with appropriate status code:
            - 400: CONFIG_INVALID or ValueError
            - 500: DEVICE_CREATION_FAILED
            - 503: PLATFORM_UNAVAILABLE
    """
    logger.info(
        "Creating device via facade router: tenant_name=%s, device_template_uuid=%s",
        request.tenant_name,
        request.device_template_uuid,
    )

    try:
        result = await facade.create_device(
            tenant_name=request.tenant_name,
            device_template_uuid=request.device_template_uuid,
            detail_config=request.detail_config,
        )
        logger.info("Device created successfully: %s", result)
        return ApiResponse(data=cast(ArcaCreationResult | SigmaCreationResult, result))

    except DeviceFacadeException as e:
        logger.error("Device creation failed: %s", e.message)
        raise _handle_facade_exception(e)

    except Exception as e:
        logger.error("Unexpected error during device creation: %s", e)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": str(e),
                "context": {"operation": "create_device"},
            },
        )


@router.delete("/devices/{paas_device_id}", response_model=ApiResponse[SuccessResponse])
@inject
async def destroy_device(
    paas_device_id: Annotated[
        str, Path(description="设备ID，格式：device_id@template_id")
    ],
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> ApiResponse[SuccessResponse]:
    """Destroy a device by its paas_device_id.

    Args:
        paas_device_id: Device ID with optional @template_id suffix
                       (e.g., "sandbox-abc123@42")

    Returns:
        SuccessResponse indicating device was destroyed

    Raises:
        HTTPException: On destruction failure with appropriate status code:
            - 404: DEVICE_NOT_FOUND
            - 500: DEVICE_DESTROY_FAILED
    """
    logger.info("Destroying device via facade router: %s", paas_device_id)

    try:
        await facade.destroy_device(paas_device_id=paas_device_id)
        logger.info("Device destroyed successfully: %s", paas_device_id)
        return ApiResponse(
            data=SuccessResponse(message="Device destroyed successfully")
        )

    except DeviceFacadeException as e:
        logger.error("Device destruction failed: %s", e.message)
        raise _handle_facade_exception(e)

    except Exception as e:
        logger.error("Unexpected error during device destruction: %s", e)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": str(e),
                "context": {
                    "operation": "destroy_device",
                    "paas_device_id": paas_device_id,
                },
            },
        )


@router.post(
    "/devices/{paas_device_id}/commands", response_model=ApiResponse[CommandResult]
)
@inject
async def execute_command(
    paas_device_id: Annotated[
        str, Path(description="设备ID，格式：device_id@template_id")
    ],
    request: ExecuteCommandRequest,
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> ApiResponse[CommandResult]:
    """Execute a command on a device.

    Args:
        paas_device_id: Device ID with optional @template_id suffix
        request: Command execution request with cmd and optional env

    Returns:
        CommandResult with execution output

    Raises:
        HTTPException: On execution failure with appropriate status code:
            - 404: DEVICE_NOT_FOUND
            - 500: COMMAND_EXECUTION_FAILED
            - 503: DEVICE_UNAVAILABLE
            - 504: COMMAND_TIMEOUT
    """
    logger.info(
        "Executing command via facade router: device=%s, cmd=%s",
        paas_device_id,
        request.cmd[:50],
    )

    try:
        result = await facade.execute_command(
            paas_device_id=paas_device_id,
            cmd=request.cmd,
            env=request.env,
        )
        logger.info(
            "Command executed successfully: device=%s, exit_code=%s",
            paas_device_id,
            result.exit_code,
        )
        return ApiResponse(data=result)

    except DeviceFacadeException as e:
        logger.error("Command execution failed: %s", e.message)
        raise _handle_facade_exception(e)

    except Exception as e:
        logger.error("Unexpected error during command execution: %s", e)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": str(e),
                "context": {
                    "operation": "execute_command",
                    "paas_device_id": paas_device_id,
                },
            },
        )


@router.get(
    "/devices/{paas_device_id}/ws-info",
    response_model=ApiResponse[WsConnectionInfoResponse],
    responses={
        404: {"model": WsConnectionErrorResponse, "description": "Device not found"},
        409: {"model": WsConnectionErrorResponse, "description": "Device not active"},
        501: {
            "model": WsConnectionErrorResponse,
            "description": "Sigma not implemented",
        },
    },
    summary="Get WebSocket connection info",
    description="Resolve WebSocket connection information for a device. Returns URL and token for direct WebSocket connection via agentclawproxy gateway.",
)
@inject
async def get_ws_connection_info(
    paas_device_id: str = Path(
        ...,
        description="PaaS device ID (e.g., ARCA-SANDBOX-xxx@tenant)",
    ),
    port: int = Query(
        ...,
        description="Target WS port on device container",
        ge=1,
        le=65535,
    ),
    path: str = Query(
        ...,
        description="WebSocket path on device (e.g., /api/openclaw/ws)",
    ),
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> ApiResponse[WsConnectionInfoResponse]:
    """Get WebSocket connection info for direct device access.

    This endpoint resolves and returns connection credentials for establishing
    a direct WebSocket connection to the specified device. The caller is
    responsible for establishing the actual WebSocket connection using the
    returned ws_url and token via the agentclawproxy gateway.

    Args:
        paas_device_id: Platform-specific device ID
        port: Target port on the device's WebSocket service (1-65535)
        path: WebSocket path on device (e.g., /api/openclaw/ws)

    Returns:
        WsConnectionInfoResponse with ws_url, token, target, expires_at

    Raises:
        HTTPException 404: Device not found
        HTTPException 409: Device exists but is not active
        HTTPException 501: Sigma platform not yet implemented
        HTTPException 500: Token generation or other error
    """
    from secbaas.community.api.bot_runtime import WsConnectionInfo

    logger.info(
        "Getting WebSocket connection info: device=%s, port=%s, path=%s",
        paas_device_id,
        port,
        path,
    )

    try:
        # Resolve connection info through facade
        conn_info: WsConnectionInfo = await facade.resolve_ws_conn_info(
            paas_device_id=paas_device_id,
            port=port,
            path=path,
        )

        logger.info(
            "WebSocket connection info resolved: device=%s, target=%s",
            paas_device_id,
            conn_info.target,
        )

        # Return response model
        return ApiResponse(
            data=WsConnectionInfoResponse(
                ws_url=conn_info.ws_url,
                token=conn_info.token,
                target=conn_info.target,
                expires_at=conn_info.expires_at,
            )
        )

    except DeviceNotFoundException as e:
        logger.warning(
            "Device not found for WebSocket connection: %s",
            paas_device_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "DEVICE_NOT_FOUND",
                "message": str(e),
                "paas_device_id": e.paas_device_id,
            },
        )

    except DeviceNotActiveException as e:
        logger.warning(
            "Device not active for WebSocket connection: %s, status=%s",
            paas_device_id,
            e.device_status,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "DEVICE_NOT_ACTIVE",
                "message": str(e),
                "paas_device_id": e.paas_device_id,
            },
        )

    except NotImplementedError as e:
        logger.warning(
            "Platform not implemented for WebSocket: %s",
            paas_device_id,
        )
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "NOT_IMPLEMENTED",
                "message": str(e),
            },
        )

    except DeviceFacadeException as e:
        logger.error(
            "Facade error getting WebSocket connection info: %s",
            e.message,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": e.original_error.code.value
                if e.original_error
                else "FACADE_ERROR",
                "message": str(e),
            },
        )


@router.get(
    "/devices/{paas_device_id}/info",
    response_model=ApiResponse[DeviceInfoResponse],
    responses={
        404: {"model": PaasErrorResponse, "description": "Device not found"},
        500: {"model": PaasErrorResponse, "description": "Internal server error"},
    },
    summary="Get device info",
    description="Get device information from the underlying PaaS platform.",
)
@inject
async def get_device_info(
    paas_device_id: Annotated[
        str, Path(description="设备ID，格式：device_id@template_id")
    ],
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> ApiResponse[DeviceInfoResponse]:
    """Get device information by its paas_device_id.

    Args:
        paas_device_id: Device ID with optional @template_id suffix
                       (e.g., "sandbox-abc123@42")

    Returns:
        DeviceInfoResponse containing device details from the PaaS platform.

    Raises:
        HTTPException: On retrieval failure with appropriate status code:
            - 404: DEVICE_NOT_FOUND
            - 500: Internal server error
    """
    logger.info("Getting device info via facade router: %s", paas_device_id)

    try:
        result: DeviceInfo = await facade.get_device_info(paas_device_id=paas_device_id)
        logger.info(
            "Device info retrieved successfully: %s, platform=%s, status=%s",
            paas_device_id,
            result.platform,
            result.status,
        )
        return ApiResponse(
            data=DeviceInfoResponse(
                platform=result.platform,
                status=result.status,
                sandbox_id=getattr(result, "sandbox_id", None),
                template_id=getattr(result, "template_id", None),
                ip_address=getattr(result, "ip_address", None),
                ttl_seconds=getattr(result, "ttl_seconds", None),
                created_at=getattr(result, "created_at", None),
                name=getattr(result, "name", None),
                description=getattr(result, "description", None),
            )
        )

    except DeviceFacadeException as e:
        logger.error("Get device info failed: %s", e.message)
        raise _handle_facade_exception(e)

    except Exception as e:
        logger.error("Unexpected error getting device info: %s", e)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": str(e),
                "context": {
                    "operation": "get_device_info",
                    "paas_device_id": paas_device_id,
                },
            },
        )


@router.put(
    "/devices/{paas_device_id}/outbound-rule",
    response_model=ApiResponse[SuccessResponse],
    responses={
        404: {"model": PaasErrorResponse, "description": "Device not found"},
        500: {"model": PaasErrorResponse, "description": "Internal server error"},
        503: {"model": PaasErrorResponse, "description": "Device unavailable"},
    },
    summary="Update outbound operation rule",
    description="Update outbound operation rule for a device. This controls outbound traffic rules for the device container.",
)
@inject
async def update_outbound_operation_rule(
    paas_device_id: Annotated[
        str,
        Path(description="设备ID，格式：device_id@template_id"),
    ],
    outbound_operation_rule: OutBoundOperationRule,
    mode: Annotated[
        OutBoundOperationRuleUpdatedMode | None,
        Query(description="更新模式：replace(默认) 或 append。不传时默认 replace"),
    ] = None,
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> ApiResponse[SuccessResponse]:
    """Update outbound operation rule for a device.

    Args:
        paas_device_id: Device ID with optional @template_id suffix
                       (e.g., "sandbox-abc123@42")
        outbound_operation_rule: New outbound operation rule to apply.
            Defines allow/deny rules for outbound traffic from the device.

    Returns:
        SuccessResponse indicating the rule was updated successfully.

    Raises:
        HTTPException: On update failure with appropriate status code:
            - 404: DEVICE_NOT_FOUND
            - 500: Internal server error
            - 503: DEVICE_UNAVAILABLE
    """
    logger.info(
        "Updating outbound operation rule via facade router: %s", paas_device_id
    )

    try:
        await facade.update_outbound_operation_rule(
            paas_device_id=paas_device_id,
            outbound_operation_rule=outbound_operation_rule,
            mode=mode,
        )
        logger.info("Outbound operation rule updated successfully: %s", paas_device_id)
        return ApiResponse(
            data=SuccessResponse(message="Outbound operation rule updated successfully")
        )

    except DeviceFacadeException as e:
        logger.error("Update outbound operation rule failed: %s", e.message)
        raise _handle_facade_exception(e)

    except Exception as e:
        logger.error("Unexpected error updating outbound operation rule: %s", e)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": str(e),
                "context": {
                    "operation": "update_outbound_operation_rule",
                    "paas_device_id": paas_device_id,
                },
            },
        )


@router.api_route(
    "/devices/{paas_device_id}/invoke-http/{port}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
    summary="Invoke HTTP request in device (transparent proxy)",
    description="Transparent proxy for HTTP requests to internal container services.",
)
@inject
async def invoke_http_in_device(
    paas_device_id: Annotated[
        str, Path(description="设备ID，格式: device_id@template_id")
    ],
    port: Annotated[int, Path(ge=1, le=65535, description="目标端口 (1-65535)")],
    path: Annotated[str, Path(description="转发路径")],
    request: Request,
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> Response:
    """Proxy HTTP request to internal service running inside a device container.

    Acts as a transparent proxy, forwarding the HTTP method, headers, body,
    and path to the internal service via the PaaS layer's invoke_http_in_device.

    Args:
        paas_device_id: Device ID with optional @template_id suffix
        port: Target port on the container (1-65535)
        path: Target path (forwarded as-is)
        request: FastAPI Request object for raw access to body/headers/query

    Returns:
        Response from internal service with original status code, headers, body.

    Raises:
        HTTPException:
            - 404: DEVICE_NOT_FOUND
            - 501: NOT_IMPLEMENTED (non-LOCAL platforms)
            - 500: Internal server error
    """
    method = request.method
    content_type = request.headers.get("content-type", "unknown")
    body_size = 0

    try:
        # Read request body
        body_bytes = await request.body()
        body_size = len(body_bytes)

        # Normalize path with leading slash
        full_path = "/" + path if not path.startswith("/") else path

        # Build query string (None if empty, "?key=value" format if present)
        query_string = "?" + request.url.query if request.url.query else None

        # Filter hop-by-hop headers
        filtered_headers = _filter_headers(dict(request.headers))

        # Security-conscious logging (no body content, no sensitive headers)
        logger.info(
            "Invoke HTTP in device: device=%s, port=%s, method=%s, "
            "path=%s, query_present=%s, content_type=%s, body_size=%s",
            paas_device_id,
            port,
            method,
            full_path,
            bool(query_string),
            content_type,
            body_size,
        )

        # Call facade method
        result = await facade.invoke_http_in_device(
            paas_device_id=paas_device_id,
            method=method,
            port=port,
            path=full_path,
            query_string=query_string,
            headers=filtered_headers,
            body=body_bytes,
        )

        # Decode base64 response body
        body_b64 = result.get("body", "")
        try:
            response_body = base64.b64decode(body_b64) if body_b64 else b""
        except (binascii.Error, ValueError) as e:
            logger.error(
                "Invalid base64 in response body for device=%s, error=%s",
                paas_device_id,
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "INVALID_RESPONSE",
                    "message": "Invalid response encoding from device",
                },
            )

        # Filter hop-by-hop headers from response
        response_headers = _filter_headers(result.get("headers", {}))

        logger.info(
            "Invoke HTTP completed: device=%s, port=%s, method=%s, "
            "status_code=%s, response_size=%s",
            paas_device_id,
            port,
            method,
            result["status_code"],
            len(response_body),
        )

        return Response(
            content=response_body,
            status_code=result["status_code"],
            headers=response_headers,
        )

    except NotImplementedError as e:
        logger.warning(
            "HTTP invocation not supported for device: %s, error=%s",
            paas_device_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "NOT_IMPLEMENTED",
                "message": str(e),
            },
        )

    except DeviceFacadeException as e:
        logger.error(
            "Invoke HTTP failed: device=%s, port=%s, error=%s",
            paas_device_id,
            port,
            e.message,
        )
        raise _handle_facade_exception(e)

    except Exception as e:
        logger.error(
            "Unexpected error during invoke HTTP: device=%s, port=%s, error=%s",
            paas_device_id,
            port,
            e,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": str(e),
                "context": {
                    "operation": "invoke_http_in_device",
                    "paas_device_id": paas_device_id,
                    "port": port,
                    "path": path,
                },
            },
        )


@router.post(
    "/devices/{paas_device_id}/open-folder",
    response_model=ApiResponse[SuccessResponse],
    responses={
        404: {
            "model": PaasErrorResponse,
            "description": "Device not found",
        },
        500: {
            "model": PaasErrorResponse,
            "description": "Internal server error",
        },
        501: {
            "model": PaasErrorResponse,
            "description": "Not implemented for platform",
        },
    },
    summary="Open folder in device file explorer",
    description=(
        "Open a folder in the device's file explorer. "
        "Only supported on LOCAL platform. Other platforms return 501."
    ),
)
@inject
async def open_folder(
    paas_device_id: Annotated[
        str, Path(description="设备ID，格式: device_id@template_id")
    ],
    request: OpenFolderRequest | None = None,
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> ApiResponse[SuccessResponse]:
    """Open a folder in the device's file explorer.

    Args:
        paas_device_id: Device ID with optional @template_id suffix
            (e.g., "container--machine--user@42" for LOCAL platform).
        request: Optional request body with folder_path.

    Returns:
        Dict with {"ok": true} if the open-folder command was sent successfully.

    Raises:
        HTTPException 404: Device not found
        HTTPException 500: Internal server error
        HTTPException 501: Operation not supported for this platform
    """
    folder_path = request.folder_path if request else None

    logger.info(
        "Open folder request: paas_device_id=%s, folder_path=%s",
        paas_device_id,
        folder_path,
    )

    try:
        result = await facade.open_folder(
            paas_device_id=paas_device_id, folder_path=folder_path
        )
        logger.info("Open folder succeeded: paas_device_id=%s", paas_device_id)
        return ApiResponse(
            data=SuccessResponse(
                success=result,
                message="Open folder command sent successfully",
            )
        )

    except NotImplementedError as e:
        logger.warning(
            "Open folder not implemented for device: %s, error=%s",
            paas_device_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "NOT_IMPLEMENTED",
                "message": str(e),
            },
        )

    except DeviceFacadeException as e:
        logger.error(
            "Open folder facade error: device=%s, error=%s",
            paas_device_id,
            e.message,
        )
        raise _handle_facade_exception(e)

    except Exception as e:
        logger.error(
            "Unexpected error during open folder: device=%s, error=%s",
            paas_device_id,
            e,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": str(e),
                "context": {
                    "operation": "open_folder",
                    "paas_device_id": paas_device_id,
                },
            },
        )


@router.put(
    "/devices/{paas_device_id}/ttl",
    response_model=ApiResponse[TTLInfoResponse],
    responses={
        404: {"model": PaasErrorResponse, "description": "Device not found"},
        500: {"model": PaasErrorResponse, "description": "Internal server error"},
        501: {
            "model": PaasErrorResponse,
            "description": "Not implemented for platform",
        },
    },
    summary="Update device TTL",
    description="Extend device TTL. Arca platform extends to now+24h. Other platforms may raise NotImplementedError.",
)
@inject
async def update_device_ttl(
    paas_device_id: Annotated[
        str, Path(description="设备ID，格式: device_id@template_id")
    ],
    facade: PaasServiceFacade = Depends(
        Provide[ApplicationContainer.services.paas_facade]
    ),
) -> ApiResponse[TTLInfoResponse]:
    """Update device TTL (time-to-live).

    Extends the device's expiration time. The extension strategy is platform-specific:
    - ARCA platform: Extends to now+24h
    - SIGMA/LOCAL platforms: May raise NotImplementedError

    Args:
        paas_device_id: Device ID with optional @template_id suffix
                       (e.g., "sandbox-abc123@42")

    Returns:
        TTLInfoResponse containing old/new expiration times and success status.

    Raises:
        HTTPException:
            - 404: DEVICE_NOT_FOUND
            - 500: Internal server error
            - 501: Not implemented for platform
    """
    logger.info("Updating device TTL: %s", paas_device_id)

    try:
        result = await facade.update_device_ttl(paas_device_id)

        logger.info(
            "Device TTL updated: %s, success=%s, skipped=%s",
            paas_device_id,
            result.success,
            result.skipped,
        )

        return ApiResponse(
            data=TTLInfoResponse(
                paas_device_id=result.paas_device_id,
                old_expiration_time=result.old_expiration_time,
                new_expiration_time=result.new_expiration_time,
                success=result.success,
                skipped=result.skipped,
                error=result.error,
            )
        )

    except NotImplementedError as e:
        logger.warning(
            "TTL update not implemented for device: %s, error=%s", paas_device_id, e
        )
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error_code": "NOT_IMPLEMENTED",
                "message": str(e),
                "context": {
                    "operation": "update_device_ttl",
                    "paas_device_id": paas_device_id,
                },
            },
        )

    except DeviceFacadeException as e:
        logger.error("Update device TTL failed: %s", e.message)
        raise _handle_facade_exception(e)

    except Exception as e:
        logger.error("Unexpected error updating device TTL: %s", e)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "INTERNAL_ERROR",
                "message": str(e),
                "context": {
                    "operation": "update_device_ttl",
                    "paas_device_id": paas_device_id,
                },
            },
        )
