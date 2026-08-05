"""Bot management REST API routes (testing endpoints).

Exposes BotManagementService methods as HTTP endpoints for testing.
These endpoints are synchronous and return immediately.

Endpoints:
- GET /api/v1/bots - List bots
- GET /api/v1/bots/{bot_uuid}/detail-by-uuid - Get bot records with devices by UUID
- GET /api/v1/bots/{bot_id}/detail-by-id - Get bot with devices by ID
- POST /api/v1/bots - Create bot
- GET /api/v1/bots/{bot_uuid} - Get bot details
- POST /api/v1/bots/{bot_uuid}/destroy - Destroy bot
- POST /api/v1/bots/{bot_uuid}/update - Update bot
- POST /api/v1/bots/{bot_uuid}/scale - Scale bot devices
- POST /api/v1/bots/{bot_uuid}/restart - Restart bot
- POST /api/v1/bots/{bot_uuid}/update-devices - Update specified bot devices
- GET /api/v1/bots/{bot_uuid}/sessions - List bot sessions
- GET /api/v1/bots/{bot_uuid}/devices - List devices by bot UUID
- GET /api/v1/bots/{bot_id}/devices-by-id - List devices by bot ID

Note: All endpoints use bot_uuid (business UUID) for bot identification,
not the internal database id.
"""

from typing import Annotated

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse, BaseRequest
from secbaas.community.api.bot_manage import (
    BotConfig,
    BotDeviceStatusResponse,
    BotListResponse,
    BotManageService,
    BotResponse,
    CreateBotResponse,
    DestroyBotResponse,
    RestartBotResponse,
    ScaleBotResponse,
    StopBotResponse,
    UpdateBotResponse,
    UpdateDevicesResponse,
)
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import DeviceListResponse
from secbaas.community.api.publish_manage import (
    BotPublishSummary,
    PublishService,
    RestartScope,
)
from secbaas.community.bootstrap import ApplicationContainer, Provide
from secbaas.community.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot管理(测试)"])


class CreateBotRequest(BaseRequest):
    """Create bot request."""

    name: str = Field(..., min_length=1, max_length=128)
    template_uuid: str = Field(..., min_length=1, max_length=64)
    device_count: int = Field(default=1, ge=1, le=100)
    operator: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=512)

    # Bot config for entity and deploy settings
    config: BotConfig | None = Field(default=None, description="Bot configuration")


class UpdateBotRequest(BaseModel):
    """Update bot request."""

    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    template_uuid: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Optional device template UUID for the UPDATE publish",
    )
    operator: str = Field(default="", max_length=64)
    request_id: str | None = Field(
        default=None,
        max_length=128,
        description="Request ID (required when config is provided)",
    )

    # Bot config for entity and deploy settings
    config: BotConfig | None = Field(default=None, description="Bot configuration")


class ScaleBotRequest(BaseRequest):
    """Scale bot request."""

    target_count: int = Field(..., ge=1, le=100)
    operator: str = Field(..., min_length=1, max_length=64)
    auto_approve_publish: bool = Field(
        default=False,
        description="When True, auto-approve all publish stage gates without manual intervention",
    )
    config: BotConfig | None = Field(
        default=None,
        description="Bot configuration for the scale publish workflow (merged with existing config; not persisted to DB)",
    )


class RestartBotRequest(BaseRequest):
    """Restart bot request."""

    operator: str = Field(..., min_length=1, max_length=64)
    auto_approve_publish: bool = Field(
        default=False,
        description="When True, auto-approve all publish stage gates without manual intervention",
    )
    scope: RestartScope = Field(
        default=RestartScope.ALL,
        description="Restart scope: 'all' (ACTIVE+FAILED) or 'unhealthy' (FAILED only)",
    )


class UpdateDevicesRequest(BaseRequest):
    """Targeted device update request."""

    operator: str = Field(..., min_length=1, max_length=64)
    device_uuids: list[str] = Field(
        ...,
        min_length=1,
        description="List of device UUIDs to update (must belong to the bot)",
    )
    auto_approve_publish: bool = Field(
        default=True,
        description="When True, auto-approve all publish stage gates without manual intervention",
    )
    config: BotConfig | None = Field(default=None, description="Bot configuration")


class StopBotRequest(BaseRequest):
    """Stop bot request."""

    operator: str = Field(..., min_length=1, max_length=64)
    request_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Request ID for correlation (client-provided)",
    )
    auto_approve_publish: bool = Field(
        default=False,
        description="When True, auto-approve all publish stage gates without manual intervention",
    )


class DestroyBotRequest(BaseRequest):
    """Destroy bot request."""

    operator: str = Field(..., min_length=1, max_length=64)
    request_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Request ID for correlation (client-provided)",
    )
    auto_approve_publish: bool = Field(
        default=False,
        description="When True, auto-approve all publish stage gates without manual intervention",
    )


@router.get("", response_model=ApiResponse[BotListResponse])
@inject
async def list_bots(
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[BotListResponse]:
    """List bots with pagination and optional status filter."""
    result = await service.list_bots(
        tenant=tenant,
        page=page,
        page_size=page_size,
        status=status,
    )
    return ApiResponse(data=result)


@router.get(
    "/{bot_uuid}/publishes",
    response_model=ApiResponse[list[BotPublishSummary]],
    summary="List all publish workflows for a bot",
    description=(
        "Return every publish workflow tied to a bot_uuid (across all its bot "
        "records and statuses), newest first. Read-only. Backs client-side "
        "idempotency recovery: differencing the returned workflow ids against a "
        "local ledger identifies an in-doubt workflow to adopt. 404 when the "
        "bot_uuid is unknown."
    ),
)
@inject
async def list_bot_publishes(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[list[BotPublishSummary]]:
    summaries = await service.list_publishes_by_bot_uuid(
        tenant=tenant, bot_uuid=bot_uuid
    )
    if not summaries:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "BOT_NOT_FOUND",
                "message": f"Bot not found or has no publishes: {bot_uuid}",
            },
        )
    return ApiResponse(data=summaries)


@router.get("/{bot_uuid}/detail-by-uuid", response_model=ApiResponse[BotListResponse])
@inject
async def get_bot_detail_by_uuid(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[BotListResponse]:
    """Get all bot records matching a bot_uuid with their devices.

    A bot_uuid may have multiple records (different statuses).
    Each record includes its associated device list.
    """
    result = await service.list_bots_with_devices_by_uuid(
        tenant=tenant, bot_uuid=bot_uuid
    )
    return ApiResponse(
        data=BotListResponse(
            items=result, total=len(result), page=1, page_size=len(result)
        )
    )


@router.get("/{bot_id}/detail-by-id", response_model=ApiResponse[BotResponse])
@inject
async def get_bot_detail_by_id(
    bot_id: Annotated[int, Path(description="Bot ID (internal)")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[BotResponse]:
    """Get a single bot with devices by unique bot_id."""
    bot = await service.get_bot_with_devices(tenant=tenant, bot_id=bot_id)
    if not bot:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "BOT_NOT_FOUND",
                "message": f"Bot not found: bot_id={bot_id}",
            },
        )
    return ApiResponse(data=bot)


@router.post("", response_model=ApiResponse[CreateBotResponse])
@inject
async def create_bot(
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: CreateBotRequest = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[CreateBotResponse]:
    """Create a new bot.

    Creates bot with specified device count through publish workflow.
    Returns the created bot with publish_id for tracking the provisioning workflow.
    """
    result = await service.create_bot(
        tenant=tenant,
        name=request.name,
        template_uuid=request.template_uuid,
        device_count=request.device_count,
        operator=request.operator,
        description=request.description,
        config=request.config,
        request_id=request.request_id,
    )
    return ApiResponse(data=result)


@router.get("/{bot_uuid}", response_model=ApiResponse[BotResponse])
@inject
async def get_bot(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    health_check: Annotated[
        bool, Query(description="When True, perform real-time device health checks")
    ] = False,
    engine_type: Annotated[
        str | None,
        Query(
            description="Optional engine override for health check strategy resolution"
        ),
    ] = None,
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[BotResponse]:
    """Get bot details by UUID.

    Optionally performs real-time device health checks when health_check=true.
    The engine_type parameter can override which engine's health checkers to use.
    """
    bot = await service.get_bot(
        tenant=tenant,
        bot_uuid=bot_uuid,
        health_check=health_check,
        engine_type=engine_type,
    )
    if not bot:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "BOT_NOT_FOUND",
                "message": f"Bot not found: {bot_uuid}",
            },
        )
    return ApiResponse(data=bot)


@router.post("/{bot_uuid}/destroy", response_model=ApiResponse[DestroyBotResponse])
@inject
async def destroy_bot(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: DestroyBotRequest = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[DestroyBotResponse]:
    """Destroy a bot.

    Initiates bot destruction through publish workflow.
    Returns bot info with publish_id for workflow tracking.
    """
    result = await service.destroy_bot(
        tenant=tenant,
        bot_uuid=bot_uuid,
        operator=request.operator,
        request_id=request.request_id,
        auto_approve_publish=request.auto_approve_publish,
    )
    if not result:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "BOT_NOT_FOUND",
                "message": f"Bot not found or already destroyed: {bot_uuid}",
            },
        )
    return ApiResponse(data=result)


@router.post("/{bot_uuid}/stop", response_model=ApiResponse[StopBotResponse])
@inject
async def stop_bot(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: StopBotRequest = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[StopBotResponse]:
    result = await service.stop_bot(
        tenant=tenant,
        bot_uuid=bot_uuid,
        operator=request.operator,
        request_id=request.request_id,
        auto_approve_publish=request.auto_approve_publish,
    )
    return ApiResponse(data=result)


@router.post("/{bot_uuid}/update", response_model=ApiResponse[UpdateBotResponse])
@inject
async def update_bot(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: UpdateBotRequest = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[UpdateBotResponse]:
    """Update bot metadata. Config changes trigger UPDATE publish."""
    bot = await service.update_bot(
        tenant=tenant,
        bot_uuid=bot_uuid,
        operator=request.operator,
        bot_name=request.name,
        bot_desc=request.description,
        bot_config=request.config,
        request_id=request.request_id,
        template_uuid=request.template_uuid,
    )
    return ApiResponse(data=bot)


@router.post("/{bot_uuid}/scale", response_model=ApiResponse[ScaleBotResponse])
@inject
async def scale_bot(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: ScaleBotRequest = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[ScaleBotResponse]:
    """Scale bot to target device count.

    Initiates scaling through publish workflow.
    Returns bot info with target_count and publish_id for workflow tracking.
    """
    result = await service.scale_bot(
        tenant=tenant,
        bot_uuid=bot_uuid,
        target_count=request.target_count,
        operator=request.operator,
        request_id=request.request_id,
        auto_approve_publish=request.auto_approve_publish,
        bot_config=request.config,
    )
    return ApiResponse(data=result)


@router.post("/{bot_uuid}/restart", response_model=ApiResponse[RestartBotResponse])
@inject
async def restart_bot(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: RestartBotRequest = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[RestartBotResponse]:
    """Restart bot devices.

    Initiates device restart through publish workflow.
    Returns bot info with publish_id for workflow tracking.
    """
    result = await service.restart_bot(
        tenant=tenant,
        bot_uuid=bot_uuid,
        operator=request.operator,
        request_id=request.request_id,
        scope=request.scope,
        auto_approve_publish=request.auto_approve_publish,
    )
    return ApiResponse(data=result)


@router.get(
    "/{bot_uuid}/device-status",
    response_model=ApiResponse[BotDeviceStatusResponse],
)
@inject
async def get_bot_device_status(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[BotDeviceStatusResponse]:
    """Get aggregate device status for a bot.

    Returns whether ALL devices are online, ALL offline, or a partial mix,
    along with detailed device counts for transparency.

    Raises HTTP 404 with BOT_NOT_FOUND if the bot UUID does not exist.
    """
    try:
        result = await service.get_bot_device_status(tenant=tenant, bot_uuid=bot_uuid)
    except BotNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "BOT_NOT_FOUND",
                "message": f"Bot not found: {bot_uuid}",
            },
        )
    return ApiResponse(data=result)


@router.get("/{bot_uuid}/devices", response_model=ApiResponse[list[DeviceListResponse]])
@inject
async def get_bot_devices_by_uuid(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[list[DeviceListResponse]]:
    """Get devices for all bot records matching a bot_uuid.

    A bot_uuid may map to multiple records (different statuses).
    Returns a list of device lists, one per matching bot record.
    """
    result = await service.list_devices_by_bot_uuid(
        tenant=tenant,
        bot_uuid=bot_uuid,
    )
    return ApiResponse(data=result)


@router.post(
    "/{bot_uuid}/update-devices", response_model=ApiResponse[UpdateDevicesResponse]
)
@inject
async def update_devices(
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: UpdateDevicesRequest = ...,  # type: ignore[assignment]
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[UpdateDevicesResponse]:
    """Update specified bot devices by UUID.

    Initiates device-level destroy+recreate cycle through publish workflow.
    Only the specified devices are affected - bot record status is unchanged.
    Returns bot info with publish_id for workflow tracking.
    """
    try:
        result = await service.update_devices(
            tenant=tenant,
            bot_uuid=bot_uuid,
            operator=request.operator,
            request_id=request.request_id,
            device_uuids=request.device_uuids,
            auto_approve_publish=request.auto_approve_publish,
            config=request.config,
        )
    except BotNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "BOT_NOT_FOUND",
                "message": f"Bot not found: {bot_uuid}",
            },
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "INVALID_REQUEST",
                "message": str(e),
            },
        )
    return ApiResponse(data=result)


@router.get("/{bot_id}/devices-by-id", response_model=ApiResponse[DeviceListResponse])
@inject
async def get_bot_devices_by_id(
    bot_id: Annotated[int, Path(description="Bot ID (internal)")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    service: BotManageService = Depends(
        Provide[ApplicationContainer.services.bot_management_service]
    ),
) -> ApiResponse[DeviceListResponse]:
    """Get devices for a bot by unique internal bot_id with pagination.

    Returns paginated list of devices with detailed status information.
    """
    result = await service.list_devices_by_bot_id(
        tenant=tenant,
        bot_id=bot_id,
        page=page,
        page_size=page_size,
    )
    return ApiResponse(data=result)
