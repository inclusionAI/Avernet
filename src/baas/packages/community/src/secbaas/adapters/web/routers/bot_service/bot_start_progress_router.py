"""Bot Start Progress REST API routes.

Provides an endpoint to query container startup progress on a bot's active
device, by selecting an available device and delegating to PaasServiceFacade.

Only supported on LOCAL platform.

Endpoint:
- GET /api/v1/bots/{bot_uuid}/start-progress
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Path, Query

from secbaas.api import ApiResponse
from secbaas.api.bot_manage import BotStartProgressResponse
from secbaas.api.bot_runtime import (
    BotFetchStartProgressDispatcher,
)
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

from .start_progress_error_handler import (
    _map_start_progress_error,
)

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot启动进度"])


@router.get(
    "/{bot_uuid}/start-progress",
    response_model=ApiResponse[BotStartProgressResponse],
    summary="Query bot device startup progress",
    description=(
        "Query the container startup progress for a bot's active device. "
        "The bot is resolved by UUID, an active device is selected, "
        "and the fetch_start_progress command is sent via the PaaS layer. "
        "Only supported on LOCAL platform."
    ),
)
@inject
async def get_bot_start_progress(
    bot_uuid: Annotated[str, Path(description="Bot UUID (business identifier)")],
    tenant: Annotated[str, Query(description="Tenant for isolation")],
    device_affinity: Annotated[
        str | None,
        Query(
            description="Device affinity key for consistent hashing-based sticky device selection"
        ),
    ] = None,
    dispatcher: BotFetchStartProgressDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_fetch_start_progress_dispatcher]
    ),
) -> ApiResponse[BotStartProgressResponse]:
    """Query the container startup progress for a bot's active device.

    Args:
        bot_uuid: Bot UUID to look up
        tenant: Tenant for multi-tenancy isolation
        device_affinity: Optional affinity key for sticky device selection
        dispatcher: Injected bot fetch-start-progress dispatcher

    Returns:
        BotStartProgressResponse with progress and optional error_message

    Raises:
        HTTPException 404: Bot not found or no devices found
        HTTPException 501: Operation not supported for this platform
        HTTPException 503: No active devices available
        HTTPException 500: Internal error or PaaS error
    """
    logger.info(
        f"Fetching start progress on bot: bot_uuid={bot_uuid}, "
        f"tenant={tenant}, "
        f"device_affinity={device_affinity!r}"
    )

    try:
        result = await dispatcher.dispatch_bot_fetch_start_progress(
            bot_uuid=bot_uuid,
            tenant=tenant,
            device_affinity=device_affinity,
        )

        logger.info(
            f"Start progress fetched for bot: bot_uuid={bot_uuid}, "
            f"progress={result.progress}"
        )

        return ApiResponse(data=result)

    except Exception as e:
        raise _map_start_progress_error(e, bot_uuid=bot_uuid)
