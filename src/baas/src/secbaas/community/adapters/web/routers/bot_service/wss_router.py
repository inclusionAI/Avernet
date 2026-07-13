"""Bot WebSocket Connection Resolver REST API routes.

Provides an endpoint to resolve WebSocket connection info for a bot
by selecting an available device and returning connection details.

Endpoint:
- GET /api/v1/bots/{bot_uuid}/ws-info - Get WebSocket connection info for a bot
"""

from datetime import datetime
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse
from secbaas.community.api.bot_runtime import (
    BotNotFoundError,
    BotWssDispatcher,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.community.api.device_manage import DeviceFacadeException
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot WebSocket连接"])


class BotWsConnectionInfoResponse(BaseModel):
    """WebSocket connection information response for bot."""

    ws_url: str = Field(..., description="WebSocket URL for connection")
    token: str = Field(..., description="Proxypass JWT token for authentication")
    target: str = Field(
        ..., description="Target identifier (format: {platform}_{device_id}:{port})"
    )
    expires_at: datetime = Field(..., description="Token expiration timestamp (UTC)")


class BotWsConnectionErrorResponse(BaseModel):
    """Error response for bot ws-info endpoint."""

    error: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    bot_uuid: str | None = Field(None, description="Bot UUID if applicable")


class BotWsErrorContext(BaseModel):
    """Error context for bot WebSocket resolution errors."""

    operation: str = Field(..., description="Operation that failed")
    bot_uuid: str = Field(..., description="Bot UUID")
    tenant: str | None = Field(None, description="Tenant if available")


@router.get(
    "/{bot_uuid}/ws-info",
    response_model=ApiResponse[BotWsConnectionInfoResponse],
    responses={
        404: {"model": BotWsConnectionErrorResponse, "description": "Bot not found"},
        503: {
            "model": BotWsConnectionErrorResponse,
            "description": "No active devices",
        },
        500: {"model": BotWsConnectionErrorResponse, "description": "Internal error"},
    },
    summary="Get WebSocket connection info for bot",
    description="Resolve WebSocket connection information for a bot. Returns URL and token for direct WebSocket connection via agentclawproxy gateway.",
)
@inject
async def get_bot_ws_connection_info(
    bot_uuid: Annotated[str, Path(description="Bot UUID (business identifier)")],
    port: Annotated[
        int,
        Query(description="Target WebSocket port on device (1-65535)", ge=1, le=65535),
    ],
    path: Annotated[
        str, Query(description="WebSocket path on device (e.g., /api/openclaw/ws)")
    ],
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
            description="Target a specific device UUID for WebSocket connection (optional, auto-selects if omitted)"
        ),
    ] = None,
    dispatcher: BotWssDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_wss_dispatcher]
    ),
) -> ApiResponse[BotWsConnectionInfoResponse]:
    """Get WebSocket connection info for a bot.

    Args:
        bot_uuid: Bot UUID to look up
        port: Target port on the device's WebSocket service (1-65535)
        path: WebSocket path on device (e.g., /api/openclaw/ws)
        tenant: Tenant for multi-tenancy isolation
        device_affinity: Optional affinity key for sticky device selection
        device_uuid: Optional specific device UUID to connect to (auto-selects if omitted)
        dispatcher: Injected bot WebSocket dispatcher

    Returns:
        BotWsConnectionInfoResponse with ws_url, token, target, expires_at

    Raises:
        HTTPException 404: Bot not found or no devices found
        HTTPException 503: No active devices available
        HTTPException 500: Internal error or PaaS error
    """
    logger.info(
        f"Getting WebSocket connection info: bot_uuid={bot_uuid}, "
        f"port={port}, path={path}, tenant={tenant}, "
        f"device_affinity={device_affinity!r}, "
        f"device_uuid={device_uuid!r}"
    )

    try:
        conn_info = await dispatcher.dispatch_bot_ws_conn_info(
            bot_uuid=bot_uuid,
            port=port,
            path=path,
            tenant=tenant,
            device_affinity=device_affinity,
            device_uuid=device_uuid,
        )

        logger.info(
            f"WebSocket connection resolved: bot_uuid={bot_uuid}, "
            f"target={conn_info.target}"
        )

        return ApiResponse(
            data=BotWsConnectionInfoResponse(
                ws_url=conn_info.ws_url,
                token=conn_info.token,
                target=conn_info.target,
                expires_at=conn_info.expires_at,
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
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "NO_ACTIVE_DEVICES",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except DeviceFacadeException as e:
        if e.original_error and "MACHINE_OFFLINE" in str(e.original_error.message):
            logger.warning(
                f"Facade error resolving WebSocket for bot {bot_uuid}: {e.message}"
            )
        else:
            logger.error(
                f"Facade error resolving WebSocket for bot {bot_uuid}: {e.message}"
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
        logger.error(f"Unexpected error resolving WebSocket for bot {bot_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "INTERNAL_ERROR",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
