"""Admin REST API routes for test/development utilities.

Provides internal endpoints for forcing entity states to success.
These endpoints bypass normal state machine transitions — use with caution.

Endpoints:
- POST /api/v1/admin/force-success - Force a publish and all related entities to success states
- POST /api/v1/admin/devices/{device_uuid}/status - Update a device's status directly
"""

from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from secbaas.api import ApiResponse
from secbaas.api.publish_manage import (
    ForceSuccessResult,
    PublishAdminService,
    PublishNotFoundError,
    UpdateDeviceStatusResult,
)
from secbaas.bootstrap import ApplicationContainer, Provide

router = APIRouter(prefix="/api/v1/admin", tags=["管理"])


class ForceSuccessRequest(BaseModel):
    publish_id: int = Field(..., gt=0, description="Publish ID to force to success")
    modifier: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User performing the force operation",
    )


class ForceSuccessResponse(BaseModel):
    publish_id: int = Field(..., description="Publish ID that was forced to success")
    previous_publish_status: str = Field(
        ..., description="Previous status of the publish"
    )
    batches_updated: int = Field(
        ..., ge=0, description="Number of batches updated to COMPLETED"
    )
    records_updated: int = Field(
        ..., ge=0, description="Number of records updated to SUCCESS"
    )
    devices_updated: int = Field(
        ..., ge=0, description="Number of devices updated to ACTIVE"
    )
    bot_updated: bool = Field(..., description="Whether the bot was updated to ACTIVE")


def _to_response(result: ForceSuccessResult) -> ForceSuccessResponse:
    return ForceSuccessResponse(
        publish_id=result.publish_id,
        previous_publish_status=result.previous_publish_status,
        batches_updated=result.batches_updated,
        records_updated=result.records_updated,
        devices_updated=result.devices_updated,
        bot_updated=result.bot_updated,
    )


@router.post("/force-success", response_model=ApiResponse[ForceSuccessResponse])
@inject
async def force_success_endpoint(
    tenant: Annotated[str, Query(description="Tenant name")],
    request: ForceSuccessRequest,
    admin_service: PublishAdminService = Depends(
        Provide[ApplicationContainer.services.publish_admin_service]
    ),
) -> ApiResponse[ForceSuccessResponse]:
    """Force a publish and all related entities to their success states.

    Given a publish_id, bypasses the normal state machine and sets:
    1. All publish records → result_status=SUCCESS
    2. All publish batches → status=COMPLETED
    3. All devices associated with the publish's bot → status=ACTIVE
    4. The bot → status=ACTIVE
    5. The publish → status=SUCCESS

    **WARNING**: This is a test/development tool. It intentionally bypasses
    the normal state machine transitions and does not perform any real
    device provisioning or deprovisioning.
    """
    try:
        result = await admin_service.force_success(
            publish_id=request.publish_id,
            tenant=tenant,
            modifier=request.modifier,
        )
    except PublishNotFoundError:
        raise
    return ApiResponse(data=_to_response(result))


class UpdateDeviceStatusRequest(BaseModel):
    status: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Target device status (e.g. ACTIVE, FAILED, PENDING, STOPPED)",
    )
    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User performing the status update",
    )


class UpdateDeviceStatusResponse(BaseModel):
    device_uuid: str = Field(..., description="Device UUID that was updated")
    previous_status: str = Field(..., description="Previous status of the device")
    new_status: str = Field(..., description="New status after the update")


def _to_update_status_response(
    result: UpdateDeviceStatusResult,
) -> UpdateDeviceStatusResponse:
    return UpdateDeviceStatusResponse(
        device_uuid=result.device_uuid,
        previous_status=result.previous_status,
        new_status=result.new_status,
    )


@router.post(
    "/devices/{device_uuid}/status",
    response_model=ApiResponse[UpdateDeviceStatusResponse],
)
@inject
async def update_device_status_endpoint(
    device_uuid: Annotated[
        str,
        Path(description="Device UUID to update"),
    ],
    tenant: Annotated[str, Query(description="Tenant name")],
    request: UpdateDeviceStatusRequest,
    admin_service: PublishAdminService = Depends(
        Provide[ApplicationContainer.services.publish_admin_service]
    ),
) -> ApiResponse[UpdateDeviceStatusResponse]:
    """Update a device's status directly, bypassing normal state machine transitions.

    **WARNING**: This is a test/development tool. It intentionally bypasses
    the normal state machine transitions and does not perform any real
    device provisioning or deprovisioning. Use with caution.
    """
    try:
        result = await admin_service.update_device_status(
            device_uuid=device_uuid,
            tenant=tenant,
            status=request.status,
            operator=request.operator,
        )
    except PublishNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Device {device_uuid} not found",
        )
    return ApiResponse(data=_to_update_status_response(result))
