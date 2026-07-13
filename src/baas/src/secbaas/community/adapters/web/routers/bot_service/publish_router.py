"""Publish REST API routes.

Exposes PublishService methods as HTTP endpoints.

Endpoints:
- POST /api/v1/publishes - Create a new publish
- GET /api/v1/publishes/{publish_id} - Get publish details
- GET /api/v1/publishes/{publish_id}/progress - Get publish progress
- POST /api/v1/publishes/{publish_id}/submit - Submit for approval
- POST /api/v1/publishes/{publish_id}/approve - Approve current stage
- POST /api/v1/publishes/{publish_id}/reject - Reject publish
- POST /api/v1/publishes/{publish_id}/revoke - Revoke publish
- POST /api/v1/publishes/{publish_id}/execute - Execute current stage
- POST /api/v1/publishes/{publish_id}/complete - Complete publish
- POST /api/v1/publishes/{publish_id}/retry - Retry failed publish (recovery)
"""

from typing import Annotated, Any

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse, BaseRequest
from secbaas.community.api.bot_manage import BotStartProgressResponse
from secbaas.community.api.bot_runtime import (
    BotFetchStartProgressDispatcher,
)
from secbaas.community.api.publish_manage import (
    DeviceCallbackRequest,
    PublishConfig,
    PublishProgressResponse,
    PublishResponse,
    PublishService,
    PublishType,
)
from secbaas.community.bootstrap import ApplicationContainer, Provide
from secbaas.community.logger import get_logger

from .start_progress_error_handler import (
    _map_start_progress_error,
)

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/publishes", tags=["发布管理"])


# ==============================================================================
# Request/Response Models
# ==============================================================================


class ErrorResponse(BaseModel):
    """Error response model."""

    error_code: str
    message: str


class CreatePublishRequest(BaseRequest):
    """Request model for creating a new publish."""

    bot_id: int = Field(..., gt=0, description="Bot ID to publish")
    publish_type: PublishType = Field(..., description="Type of publish operation")
    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User creating the publish",
    )
    config: PublishConfig | None = Field(
        default=None,
        description="Optional stage/drain config override",
    )


class ApproveRequest(BaseRequest):
    """Request model for approving a publish stage."""

    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User approving the publish",
    )
    comment: str | None = Field(
        default=None,
        max_length=512,
        description="Optional approval comment",
    )


class RejectRequest(BaseRequest):
    """Request model for rejecting a publish."""

    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User rejecting the publish",
    )
    reason: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Reason for rejection",
    )


class RevokeRequest(BaseRequest):
    """Request model for revoking a publish."""

    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User revoking the publish",
    )
    reason: str | None = Field(
        default=None,
        max_length=512,
        description="Optional reason for revocation",
    )


class ExecuteRequest(BaseRequest):
    """Request model for executing a publish stage."""

    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User executing the stage",
    )


class CompleteRequest(BaseRequest):
    """Request model for completing a publish."""

    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User completing the publish",
    )


class RetryRequest(BaseRequest):
    """Request model for retrying a failed publish."""

    operator: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="User retrying the publish",
    )
    config: PublishConfig | None = Field(
        default=None,
        description="Optional new config (uses original if not provided)",
    )


class DrainResultResponse(BaseModel):
    """Response model for drain/execute result."""

    success: bool = Field(
        ..., description="Whether the operation completed successfully"
    )
    sessions_remaining: int = Field(..., ge=0, description="Active sessions remaining")
    duration_seconds: float = Field(
        ..., ge=0, description="Operation duration in seconds"
    )
    timeout_reached: bool = Field(
        default=False, description="Whether timeout was reached"
    )


# ==============================================================================
# Endpoints
# ==============================================================================


@router.get(
    "/{publish_id}/start-progress",
    response_model=ApiResponse[BotStartProgressResponse],
    summary="Query bot device startup progress via publish ID",
    description=(
        "Query the container startup progress for a bot's active device using "
        "publish_id. Internally resolves publish_id to bot_uuid via "
        "PublishService.get_publish_bot_uuid() then delegates to "
        "BotFetchStartProgressDispatcher. Only supported on LOCAL platform."
    ),
)
@inject
async def get_publish_start_progress(
    publish_id: Annotated[int, Path(description="Publish ID")],
    tenant: Annotated[str, Query(description="Tenant for isolation")],
    device_affinity: Annotated[
        str | None,
        Query(
            description="Device affinity key for consistent hashing-based sticky device selection"
        ),
    ] = None,
    publish_service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
    dispatcher: BotFetchStartProgressDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_fetch_start_progress_dispatcher]
    ),
) -> ApiResponse[BotStartProgressResponse]:
    """Query the container startup progress for a bot's active device via publish_id.

    Args:
        publish_id: Publish ID to look up
        tenant: Tenant for multi-tenancy isolation
        device_affinity: Optional affinity key for sticky device selection
        publish_service: Injected publish service for publish_id -> bot_uuid resolution
        dispatcher: Injected bot fetch-start-progress dispatcher

    Returns:
        BotStartProgressResponse with progress and optional extra fields

    Raises:
        HTTPException 404: Publish not found, bot not found, or no devices found
        HTTPException 501: Operation not supported for this platform
        HTTPException 503: No active devices available
        HTTPException 500: Internal error or PaaS error
    """
    logger.info(
        f"Fetching start progress via publish: publish_id={publish_id}, "
        f"tenant={tenant}, "
        f"device_affinity={device_affinity!r}"
    )

    bot_uuid: str | None = None
    try:
        bot_uuid = await publish_service.get_publish_bot_uuid(
            tenant=tenant, publish_id=publish_id
        )

        result = await dispatcher.dispatch_bot_fetch_start_progress(
            bot_uuid=bot_uuid,
            tenant=tenant,
            device_affinity=device_affinity,
        )

        logger.info(
            f"Start progress fetched via publish: publish_id={publish_id}, "
            f"bot_uuid={bot_uuid}, progress={result.progress}"
        )

        return ApiResponse(data=result)

    except Exception as e:
        raise _map_start_progress_error(e, bot_uuid=bot_uuid, publish_id=publish_id)


@router.post("", response_model=ApiResponse[PublishResponse])
@inject
async def create_publish(
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: CreatePublishRequest = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[PublishResponse]:
    """Create a new publish operation.

    Creates a publish record with PENDING status (awaiting approval) and generates
    batch configuration based on publish type.

    Returns the created publish with ID and status.
    """
    publish = await service.create_publish(
        tenant=tenant,
        bot_id=request.bot_id,
        publish_type=request.publish_type,
        operator=request.operator,
        config=request.config,
        request_id=request.request_id,
    )
    return ApiResponse(data=publish)


@router.get(
    "/{publish_id}/progress", response_model=ApiResponse[PublishProgressResponse]
)
@inject
async def get_publish_progress(
    publish_id: Annotated[int, Path(description="Publish ID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    include_devices: Annotated[
        bool, Query(description="Include device-level details for retry")
    ] = False,
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[PublishProgressResponse]:
    """Get detailed progress information for a publish operation.

    Returns aggregated progress statistics including:
    - Overall progress percentage
    - Stage-by-stage breakdown
    - Device operation counts (success/failure)
    - Timeline information
    - Device-level details (when include_devices=true)

    Use include_devices=true to get detailed device results for retry scenarios.
    """
    progress = await service.get_publish_progress(
        tenant=tenant,
        publish_id=publish_id,
        include_devices=include_devices,
    )

    if progress is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "PUBLISH_NOT_FOUND",
                "message": f"Publish not found or access denied: {publish_id}",
            },
        )

    return ApiResponse(data=progress)


@router.get("/{publish_id}", response_model=ApiResponse[PublishResponse])
@inject
async def get_publish(
    publish_id: Annotated[int, Path(description="Publish ID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[PublishResponse]:
    """Get publish details by ID."""
    publish = await service.get_publish(
        tenant=tenant,
        publish_id=publish_id,
    )

    if publish is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "PUBLISH_NOT_FOUND",
                "message": f"Publish not found or access denied: {publish_id}",
            },
        )

    return ApiResponse(data=publish)


@router.post("/{publish_id}/approve", response_model=ApiResponse[PublishResponse])
@inject
async def approve_stage(
    publish_id: Annotated[int, Path(description="Publish ID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: ApproveRequest = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[PublishResponse]:
    """Approve current stage and proceed.

    - For PENDING status: transitions to ACTIVE
    - For APPROVING status: proceeds to next stage

    Returns updated publish status.
    """
    publish = await service.approve_stage(
        tenant=tenant,
        publish_id=publish_id,
        operator=request.operator,
        comment=request.comment,
    )
    return ApiResponse(data=publish)


@router.post("/{publish_id}/reject", response_model=ApiResponse[PublishResponse])
@inject
async def reject_publish(
    publish_id: Annotated[int, Path(description="Publish ID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: RejectRequest = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[PublishResponse]:
    """Reject a publish at an approval gate.

    Can only reject publishes in PENDING or APPROVING status.
    Transitions to REJECTED status.
    """
    publish = await service.reject_publish(
        tenant=tenant,
        publish_id=publish_id,
        operator=request.operator,
        reason=request.reason,
    )
    return ApiResponse(data=publish)


@router.post("/{publish_id}/revoke", response_model=ApiResponse[PublishResponse])
@inject
async def revoke_publish(
    publish_id: Annotated[int, Path(description="Publish ID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: RevokeRequest = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[PublishResponse]:
    """Revoke a publish after approval.

    Can only revoke publishes in APPROVING status.
    Transitions to REVOKED status.
    """
    publish = await service.revoke_publish(
        tenant=tenant,
        publish_id=publish_id,
        operator=request.operator,
        reason=request.reason,
    )
    return ApiResponse(data=publish)


@router.post("/{publish_id}/execute", response_model=ApiResponse[DrainResultResponse])
@inject
async def execute_stage(
    publish_id: Annotated[int, Path(description="Publish ID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: ExecuteRequest = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[DrainResultResponse]:
    """Execute current stage batches.

    Processes the current stage's pending batches with device drain
    and rolling update. Can only execute in ACTIVE status.

    Returns execution result including drain status.
    """
    result = await service.execute_stage(
        tenant=tenant,
        publish_id=publish_id,
        operator=request.operator,
    )
    return ApiResponse(
        data=DrainResultResponse(
            success=result.success,
            sessions_remaining=result.sessions_remaining,
            duration_seconds=result.duration_seconds,
            timeout_reached=result.timeout_reached,
        )
    )


@router.post("/{publish_id}/complete", response_model=ApiResponse[PublishResponse])
@inject
async def complete_publish(
    publish_id: Annotated[int, Path(description="Publish ID")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: CompleteRequest = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[PublishResponse]:
    """Mark publish as successfully completed.

    Transitions to SUCCESS status and increments bot version.
    Should be called after all stages are complete.
    """
    publish = await service.complete_publish(
        tenant=tenant,
        publish_id=publish_id,
        operator=request.operator,
    )
    return ApiResponse(data=publish)


@router.post("/{publish_id}/retry", response_model=ApiResponse[PublishResponse])
@inject
async def retry_publish(
    publish_id: Annotated[int, Path(description="Original publish ID to retry")],
    tenant: Annotated[str, Query(description="Tenant name")] = ...,  # type: ignore[assignment]
    request: RetryRequest = ...,  # type: ignore[assignment]
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[PublishResponse]:
    """Retry a failed publish by creating a new publish.

    Creates a new publish with the same bot_id, publish_type, and config
    as the original failed publish. The new publish starts in PENDING status.

    Only works for FAILED publishes. For other terminal states, create a new publish.
    """
    publish = await service.retry_publish(
        tenant=tenant,
        publish_id=publish_id,
        operator=request.operator,
        request_id=request.request_id,
        config=request.config,
    )
    return ApiResponse(data=publish)


# Separate router for device callback (different prefix, no publish_id in path)
callback_router = APIRouter(prefix="/api/v1/publish", tags=["发布管理"])


@callback_router.post("/device-callback", response_model=ApiResponse[dict[str, Any]])
@inject
async def device_callback(
    request: DeviceCallbackRequest,
    service: PublishService = Depends(
        Provide[ApplicationContainer.services.publish_service]
    ),
) -> ApiResponse[dict[str, Any]]:
    """Handle device start hook callback.

    Receives start hook execution results from async workers or external systems.
    Drives the state update chain: device → publish_record → batch → publish → bot.

    Idempotent: ignores callbacks for already-processed records.
    Returns 404 if device_uuid not found.
    """
    try:
        result = await service.handle_device_callback(request)
        return ApiResponse(data=result)
    except Exception as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "DEVICE_NOT_FOUND",
                    "message": str(e),
                },
            )
        raise
