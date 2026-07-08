"""Device REST API routes.

Exposes DeviceService methods as HTTP endpoints.

Endpoints:
- GET /api/v1/devices/{device_uuid} - Get device info by UUID
"""

from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from secbaas.api import ApiResponse
from secbaas.api.device_manage import DeviceResponse, DeviceService
from secbaas.bootstrap import ApplicationContainer, Provide
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/devices", tags=["设备管理"])


class DeviceNotFoundResponse(BaseModel):
    """Response model for device not found error."""

    error: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    device_uuid: str | None = Field(None, description="Device UUID if applicable")


@router.get(
    "/{device_uuid}",
    response_model=ApiResponse[DeviceResponse],
    responses={
        404: {"model": DeviceNotFoundResponse, "description": "Device not found"},
        500: {"model": DeviceNotFoundResponse, "description": "Internal server error"},
    },
    summary="Get device info by UUID",
    description="Get device full information by its global unique UUID.",
)
@inject
async def get_device_info(
    device_uuid: Annotated[
        str,
        Path(description="设备全局唯一UUID，格式：DEVICE-{hex} (e.g., DEVICE-abc123)"),
    ],
    device_service: DeviceService = Depends(
        Provide[ApplicationContainer.services.device_service]
    ),
) -> ApiResponse[DeviceResponse]:
    """Get device information by its global unique UUID.

    Args:
        device_uuid: Device UUID (physical unique key, format: DEVICE-{hex})

    Returns:
        DeviceResponse with full device information.

    Raises:
        HTTPException 404: Device not found or has been deleted
        HTTPException 500: Internal server error
    """
    logger.info(f"Getting device info via device router: device_uuid={device_uuid}")

    try:
        result = device_service.get_device_info(device_uuid=device_uuid)

        if result is None:
            logger.info(f"Device not found: {device_uuid}")
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "DEVICE_NOT_FOUND",
                    "message": f"Device not found: {device_uuid}",
                    "device_uuid": device_uuid,
                },
            )

        logger.info(
            f"Device info retrieved successfully: device_uuid={device_uuid}, "
            f"status={result.status}, tenant={result.tenant}"
        )
        return ApiResponse(data=result)

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Unexpected error getting device info: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "INTERNAL_ERROR",
                "message": str(e),
                "device_uuid": device_uuid,
            },
        )
