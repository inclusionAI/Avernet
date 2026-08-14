"""Bot HTTP Connection Resolver REST API routes.

Provides an endpoint to resolve HTTP connection info for a bot
by selecting an available device and returning connection details.

Endpoint:
- GET /api/v1/bots/{bot_uuid}/http-info - Get HTTP connection info for a bot
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse
from secbaas.community.api.bot_runtime import (
    BotHttpConnInfoDispatcher,
    BotNotFoundError,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.community.api.device_manage import DeviceFacadeException
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot HTTP连接"])


class BotHttpConnectionInfoResponse(BaseModel):
    """HTTP connection information response for bot."""

    http_url: str = Field(..., description="HTTP URL for direct connection")
    token: str = Field(..., description="Proxypass JWT token for authentication")
    target: str = Field(
        ..., description="Target identifier (format: {platform}_{device_id}:{port})"
    )


class BotHttpConnectionErrorResponse(BaseModel):
    """Error response for bot http-info endpoint."""

    error: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    bot_uuid: str | None = Field(None, description="Bot UUID if applicable")


class BotHttpErrorContext(BaseModel):
    """Error context for bot HTTP connection resolution errors."""

    operation: str = Field(..., description="Operation that failed")
    bot_uuid: str = Field(..., description="Bot UUID")
    tenant: str | None = Field(None, description="Tenant if available")


@router.get(
    "/{bot_uuid}/http-info",
    response_model=ApiResponse[BotHttpConnectionInfoResponse],
    responses={
        404: {
            "model": BotHttpConnectionErrorResponse,
            "description": "Bot not found or no active devices",
        },
        500: {"model": BotHttpConnectionErrorResponse, "description": "Internal error"},
    },
    summary="Get HTTP connection info for bot",
    description="Resolve HTTP connection information for a bot. Returns URL and token for direct HTTP connection via agentclawproxy gateway.",
)
@inject
async def get_bot_http_connection_info(
    bot_uuid: Annotated[str, Path(description="Bot UUID (business identifier)")],
    port: Annotated[
        int,
        Query(description="Target HTTP port on device (1-65535)", ge=1, le=65535),
    ],
    path: Annotated[str, Query(description="HTTP path on device (e.g., /api/health)")],
    tenant: Annotated[str, Query(description="Tenant for isolation")],
    device_affinity: Annotated[
        str | None,
        Query(
            description="Device affinity key for consistent hashing-based sticky device selection"
        ),
    ] = None,
    device_uuid: Annotated[
        str | None,
        Query(
            description="Target a specific device UUID for HTTP connection (optional, auto-selects if omitted)"
        ),
    ] = None,
    dispatcher: BotHttpConnInfoDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_http_conn_info_dispatcher]
    ),
) -> ApiResponse[BotHttpConnectionInfoResponse]:
    """Get HTTP connection info for a bot.

    Args:
        bot_uuid: Bot UUID to look up
        port: Target port on the device's HTTP service (1-65535)
        path: HTTP path on device (e.g., /api/health)
        tenant: Tenant for multi-tenancy isolation
        device_affinity: Optional affinity key for sticky device selection
        device_uuid: Optional specific device UUID to connect to (auto-selects if omitted)
        dispatcher: Injected bot HTTP connection info dispatcher

    Returns:
        BotHttpConnectionInfoResponse with http_url, token

    Raises:
        HTTPException 404: Bot not found or no devices found
        HTTPException 404: No active devices available
        HTTPException 500: Internal error or PaaS error
    """
    logger.info(
        f"Getting HTTP connection info: bot_uuid={bot_uuid}, "
        f"port={port}, path={path}, tenant={tenant}, "
        f"device_affinity={device_affinity!r}, "
        f"device_uuid={device_uuid!r}"
    )

    try:
        conn_info = await dispatcher.dispatch_bot_http_conn_info(
            bot_uuid=bot_uuid,
            port=port,
            path=path,
            tenant=tenant,
            device_affinity=device_affinity,
            device_uuid=device_uuid,
        )

        logger.info(
            f"HTTP connection resolved: bot_uuid={bot_uuid}, "
            f"http_url={conn_info.http_url}"
        )

        return ApiResponse(
            data=BotHttpConnectionInfoResponse(
                http_url=conn_info.http_url,
                token=conn_info.token,
                target=conn_info.target,
            )
        )

    except BotNotFoundError as e:
        logger.warning(f"Bot not found: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "BOT_NOT_FOUND",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except NoDevicesFoundError as e:
        logger.warning(f"No devices found for bot: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "NO_DEVICES_FOUND",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except NoActiveDevicesError as e:
        logger.warning(f"No active devices for bot: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "NO_ACTIVE_DEVICES",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except DeviceFacadeException as e:
        logger.error(
            f"Facade error resolving HTTP conn info for bot {bot_uuid}: {e.message}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": e.original_error.code.value
                if e.original_error
                else "FACADE_ERROR",
                "message": str(e),
                "context": {
                    "operation": e.operation,
                    "platform_type": e.platform_type,
                    "paas_device_id": e.paas_device_id,
                },
            },
        )

    except Exception as e:
        logger.error(
            f"Unexpected error resolving HTTP conn info for bot {bot_uuid}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "INTERNAL_ERROR",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
