"""Admin REST API routes for test/development utilities.

Provides internal endpoints for forcing entity states to success.
These endpoints bypass normal state machine transitions — use with caution.

Endpoints:
- POST /api/v1/admin/force-success - Force a publish and all related entities to success states
- POST /api/v1/admin/devices/{device_uuid}/status - Update a device's status directly
- POST /api/v1/admin/bots/{bot_id}/status - Update a bot's status directly
- POST /api/v1/admin/bots/restore - Restore a bot record to live ACTIVE (bot_id in request body)
"""

from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse
from secbaas.community.api.publish_manage import (
    ForceSuccessResult,
    PublishAdminService,
    PublishConflictError,
    PublishNotFoundError,
    RestoreBotResult,
    UpdateBotStatusResult,
    UpdateDeviceStatusResult,
)
from secbaas.community.bootstrap import ApplicationContainer, Provide

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


class UpdateBotStatusRequest(BaseModel):
    status: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Target bot status (e.g. ACTIVE, STOPPED, PENDING, FAILED)",
    )
    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User performing the status update",
    )


class UpdateBotStatusResponse(BaseModel):
    bot_id: int = Field(..., description="Bot ID that was updated")
    bot_uuid: str = Field(..., description="Bot UUID of the updated bot")
    previous_status: str = Field(..., description="Previous status of the bot")
    new_status: str = Field(..., description="New status after the update")


def _to_update_bot_status_response(
    result: UpdateBotStatusResult,
) -> UpdateBotStatusResponse:
    return UpdateBotStatusResponse(
        bot_id=result.bot_id,
        bot_uuid=result.bot_uuid,
        previous_status=result.previous_status,
        new_status=result.new_status,
    )


@router.post(
    "/bots/{bot_id}/status",
    response_model=ApiResponse[UpdateBotStatusResponse],
)
@inject
async def update_bot_status_endpoint(
    bot_id: Annotated[
        int,
        Path(description="Bot ID (primary key) to update"),
    ],
    request: UpdateBotStatusRequest,
    admin_service: PublishAdminService = Depends(
        Provide[ApplicationContainer.services.publish_admin_service]
    ),
) -> ApiResponse[UpdateBotStatusResponse]:
    """Update a bot's status directly, bypassing normal state machine transitions.

    **WARNING**: This is a test/development tool. It intentionally bypasses
    the normal state machine transitions and does not perform any real
    publish workflow or PaaS operation. Use with caution. The bot is
    resolved by its primary key (bot_id); tenant and env are sourced from
    the fetched record, not from the request.
    """
    try:
        result = await admin_service.update_bot_status(
            bot_id=bot_id,
            status=request.status,
            operator=request.operator,
        )
    except PublishNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Bot {bot_id} not found",
        )
    return ApiResponse(data=_to_update_bot_status_response(result))


class RestoreBotRequest(BaseModel):
    bot_id: int = Field(
        ...,
        gt=0,
        description="Bot ID (primary key) to restore",
    )
    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User performing the restore operation",
    )


class RestoreBotResponse(BaseModel):
    bot_id: int = Field(..., description="Bot ID that was restored")
    bot_uuid: str = Field(..., description="Bot UUID shared by the version group")
    previous_status: str = Field(..., description="Status of the bot before restore")
    previous_is_deleted: int = Field(
        ..., ge=0, description="Tombstone value of the bot before restore (0 = live)"
    )
    new_status: str = Field(..., description="Status after restore (ACTIVE)")
    retired_sibling_count: int = Field(
        ...,
        ge=0,
        description="Other live records sharing the bot_uuid retired by this call",
    )


def _to_restore_bot_response(result: RestoreBotResult) -> RestoreBotResponse:
    return RestoreBotResponse(
        bot_id=result.bot_id,
        bot_uuid=result.bot_uuid,
        previous_status=result.previous_status,
        previous_is_deleted=result.previous_is_deleted,
        new_status=result.new_status,
        retired_sibling_count=result.retired_sibling_count,
    )


@router.post(
    "/bots/restore",
    response_model=ApiResponse[RestoreBotResponse],
)
@inject
async def restore_bot_endpoint(
    request: RestoreBotRequest,
    admin_service: PublishAdminService = Depends(
        Provide[ApplicationContainer.services.publish_admin_service]
    ),
) -> ApiResponse[RestoreBotResponse]:
    """Restore a bot record to live ACTIVE, retiring its sibling records.

    In one atomic transaction: sets the bot_id target to ``is_deleted=0``
    and ``status=ACTIVE`` and retires every OTHER live record sharing its
    ``bot_uuid`` (``status=RELEASED``, soft-deleted with the tombstone
    convention). Conflicting unique-key states (e.g. a concurrent restore
    or publish race) roll the whole transaction back and surface as 409.

    The ``bot_id`` and ``operator`` travel in the JSON request body; the
    path carries no record identifier.

    **WARNING**: This is a state-machine-bypassing admin recovery tool —
    it intentionally deviates from "RELEASED is terminal" and performs no
    lifecycle validation. Cross-env by design: target lookup is by
    primary key and sibling matching by ``bot_uuid`` only; the request
    requires no tenant/env input and no env filtering is applied.
    """
    try:
        result = await admin_service.restore_bot(
            bot_id=request.bot_id,
            operator=request.operator,
        )
    except PublishConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Bot {request.bot_id} restore conflicts with a live unique-key slot; "
                f"no changes applied: {exc.message}"
            ),
        )
    except PublishNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Bot {request.bot_id} not found",
        )
    return ApiResponse(data=_to_restore_bot_response(result))
