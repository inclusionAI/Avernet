"""Session File Sharing REST API routes.

Provides endpoints for session file upload URL generation, upload completion,
upload cancellation, share-link generation, transfer status query, and transfer
deletion.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from secbaas.community.api import ApiResponse
from secbaas.community.api.session_file_sharing import (
    SessionFileSharingDispatcher,
    SessionGetTransferStatusResponse,
    TransferNotFoundError,
)
from secbaas.community.bootstrap import ApplicationContainer
from secbaas.community.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/sessions", tags=["Session文件共享"])


# ---------------------------------------------------------------------------
# Deferred DI helpers — forward references registered by Phase 79
# ---------------------------------------------------------------------------


def _get_session_file_sharing_dispatcher() -> SessionFileSharingDispatcher:
    """Resolve session_file_sharing_dispatcher at request time.

    Uses a plain Depends callable (not Provide[...] + @inject) because
    ``session_file_sharing_dispatcher`` is a Phase 79 forward reference
    that does not exist on the container at module import time.  The
    lookup is deferred to request time, after Phase 79 DI registration
    has run.
    """
    return ApplicationContainer.services.session_file_sharing_dispatcher()


@router.get(
    "/{tenant}/{session_id}/transfers/{transfer_id}",
    response_model=ApiResponse[SessionGetTransferStatusResponse],
    summary="Query Session file transfer status",
)
async def get_transfer_status(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    session_id: Annotated[str, Path(description="Session identifier")],
    transfer_id: Annotated[str, Path(description="Transfer ticket ID")],
    dispatcher: SessionFileSharingDispatcher = Depends(
        _get_session_file_sharing_dispatcher
    ),
) -> ApiResponse[SessionGetTransferStatusResponse]:
    """Query the status of a Session file transfer by transfer_id."""
    logger.info(
        f"get_transfer_status: tenant={tenant}, session_id={session_id}, "
        f"transfer_id={transfer_id}"
    )

    try:
        result = await dispatcher.dispatch_get_transfer_status(
            transfer_id=transfer_id,
            tenant=tenant,
            session_id=session_id,
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