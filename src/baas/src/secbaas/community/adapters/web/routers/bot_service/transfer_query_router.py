"""Bot File Transfer Query REST API routes.

Provides an endpoint to query transfer ticket status by transfer_id.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Path, status

from secbaas.api import ApiResponse
from secbaas.api.bot_runtime import (
    BotFileTransferDispatcher,
    GetTransferStatusResponse,
    TransferNotFoundError,
)
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot文件传输查询"])


@router.get(
    "/{tenant}/{bot_uuid}/files/transfers/{transfer_id}",
    response_model=ApiResponse[GetTransferStatusResponse],
    summary="Query file transfer status",
)
@inject
async def get_transfer_status(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    transfer_id: Annotated[str, Path(description="Transfer ticket ID")],
    dispatcher: BotFileTransferDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_file_transfer_dispatcher]
    ),
) -> ApiResponse[GetTransferStatusResponse]:
    """Query the status of a file transfer by transfer_id.

    Returns transfer status, direction, URLs (when available), and error info.
    """
    logger.info(
        f"get_transfer_status: tenant={tenant}, bot_uuid={bot_uuid}, "
        f"transfer_id={transfer_id}"
    )

    try:
        result = await dispatcher.dispatch_get_transfer_status(
            transfer_id=transfer_id,
        )
        return ApiResponse(data=result)

    except TransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except Exception as e:
        logger.error(f"Error querying transfer status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "INTERNAL_ERROR", "message": str(e)},
        )