"""Bot Open Folder REST API routes.

Provides an endpoint to open a folder on a bot's active device file explorer,
by selecting an available device and delegating to PaasServiceFacade.

Only supported on LOCAL platform.

Endpoint:
- POST /api/v1/bots/{tenant}/{bot_uuid}/open-folder
"""

import os
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator

from secbaas.api import ApiResponse, SuccessResponse
from secbaas.api.bot_runtime import (
    BotNotFoundError,
    BotOpenFolderDispatcher,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.api.device_manage import DeviceFacadeException, ErrorCode
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot文件夹操作"])


class OpenFolderRequest(BaseModel):
    """Request model for opening a folder on a bot's active device.

    Only supported on LOCAL platform. Non-LOCAL platforms return 501.
    """

    folder_path: str | None = Field(
        default=None,
        max_length=1024,
        description="Folder path to open. If None, uses platform default.",
    )

    @field_validator("folder_path")
    @classmethod
    def validate_folder_path(cls, v: str | None) -> str | None:
        """Validate folder_path to prevent directory traversal and block system paths."""
        if v is None:
            return v
        if ".." in v:
            raise ValueError("folder_path must not contain directory traversal (..)")
        blocked_prefixes = (
            "/etc",
            "/bin",
            "/sbin",
            "/boot",
            "/dev",
            "/proc",
            "/sys",
            "/root",
        )
        normalized = os.path.normpath(v)
        if any(normalized.startswith(p) for p in blocked_prefixes):
            raise ValueError(f"folder_path {v!r} targets a prohibited system directory")
        return v


@router.post(
    "/{tenant}/{bot_uuid}/open-folder",
    response_model=ApiResponse[SuccessResponse],
    summary="Open a folder on a bot's active device",
    description=(
        "Open a folder in the device's file explorer. "
        "The bot is resolved by UUID, an active device is selected, "
        "and the open-folder command is sent via the PaaS layer. "
        "Only supported on LOCAL platform."
    ),
)
@inject
async def open_folder_on_bot(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID (business identifier)")],
    request: OpenFolderRequest | None = None,
    device_affinity: Annotated[
        str | None,
        Query(
            description="Device affinity key for consistent hashing-based sticky device selection"
        ),
    ] = None,
    dispatcher: BotOpenFolderDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_open_folder_dispatcher]
    ),
) -> ApiResponse[SuccessResponse]:
    """Open a folder in a bot's active device file explorer.

    Args:
        tenant: Tenant for multi-tenancy isolation
        bot_uuid: Bot UUID to look up
        request: Optional request body with folder_path
        device_affinity: Optional affinity key for sticky device selection
        dispatcher: Injected bot open-folder dispatcher

    Returns:
        SuccessResponse with success=True if the command was sent successfully

    Raises:
        HTTPException 404: Bot not found or no devices found
        HTTPException 501: Operation not supported for this platform
        HTTPException 503: No active devices available
        HTTPException 500: Internal error or PaaS error
    """
    folder_path = request.folder_path if request else None

    logger.info(
        f"Opening folder on bot: bot_uuid={bot_uuid}, "
        f"folder_path={folder_path!r}, "
        f"tenant={tenant}, "
        f"device_affinity={device_affinity!r}"
    )

    try:
        result = await dispatcher.dispatch_bot_open_folder(
            bot_uuid=bot_uuid,
            tenant=tenant,
            folder_path=folder_path,
            device_affinity=device_affinity,
        )

        logger.info(f"Open folder completed on bot: bot_uuid={bot_uuid}")

        return ApiResponse(
            data=SuccessResponse(
                success=result,
                message="Open folder command sent successfully",
            )
        )

    except BotNotFoundError as e:
        logger.warning(f"Bot not found for open_folder: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "BOT_NOT_FOUND",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except NoDevicesFoundError as e:
        logger.warning(f"No devices found for bot in open_folder: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "NO_DEVICES_FOUND",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except NoActiveDevicesError as e:
        logger.warning(f"No active devices for bot in open_folder: {bot_uuid}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "NO_ACTIVE_DEVICES",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )

    except DeviceFacadeException as e:
        logger.error(f"Facade error opening folder on bot {bot_uuid}: {e.message}")
        is_not_implemented = (
            e.original_error is not None
            and e.original_error.code == ErrorCode.PLATFORM_ERROR
        )
        http_status = (
            status.HTTP_501_NOT_IMPLEMENTED
            if is_not_implemented
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        error_code = (
            "NOT_IMPLEMENTED"
            if is_not_implemented
            else (e.original_error.code.value if e.original_error else "FACADE_ERROR")
        )
        raise HTTPException(
            status_code=http_status,
            detail={
                "error": error_code,
                "message": str(e),
                "context": {
                    "operation": e.operation,
                    "platform_type": e.platform_type,
                    "paas_device_id": e.paas_device_id,
                },
            },
        )

    except Exception as e:
        logger.error(f"Unexpected error opening folder on bot {bot_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "INTERNAL_ERROR",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
