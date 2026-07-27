"""Session File Sharing REST API routes.

Provides endpoints for session file upload URL generation, upload completion,
upload cancellation, share-link generation, transfer status query, and transfer
deletion.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from secbaas.api import ApiResponse, DomainError
from secbaas.api.session_file_sharing import (
    SessionCancelUploadResponse,
    SessionCompleteUploadResponse,
    SessionDeleteTransferResponse,
    SessionFileSharingDispatcher,
    SessionGetTransferStatusResponse,
    SessionGetUploadUrlRequest,
    SessionGetUploadUrlResponse,
    SessionShareLinkRequest,
    SessionShareLinkResponse,
    SourceTransferNotFoundError,
    SourceTransferNotReadyError,
    StagingObjectNotFoundError,
    TransferNotFoundError,
    TransferNotTerminalError,
    TransferStateConflictError,
)
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

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


# ---------------------------------------------------------------------------
# Session File Upload
# ---------------------------------------------------------------------------


@router.post(
    "/{tenant}/{session_id}/files/upload-url",
    response_model=ApiResponse[SessionGetUploadUrlResponse],
    summary="Get a presigned upload URL for Session file upload",
)
async def get_upload_url(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    session_id: Annotated[str, Path(description="Session identifier")],
    request: SessionGetUploadUrlRequest,
    dispatcher: SessionFileSharingDispatcher = Depends(
        _get_session_file_sharing_dispatcher
    ),
) -> ApiResponse[SessionGetUploadUrlResponse]:
    """Get a pre-signed upload URL for uploading a file to OSS in Session context.

    Returns upload_url (PUT to OSS), transfer_id, and expires_at.
    Session uploads have no device_path and go directly to OSS staging.
    """
    logger.info(
        f"get_upload_url: tenant={tenant}, session_id={session_id}, "
        f"filename={request.filename}"
    )

    try:
        result = await dispatcher.dispatch_get_upload_url(
            tenant=tenant,
            session_id=session_id,
            filename=request.filename,
            expire_seconds=request.expire_seconds,
            staging_subdir=request.staging_subdir,
            file_size=request.file_size,
            part_size=request.part_size,
            operator=request.operator,
        )
        return ApiResponse(data=result)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "INVALID_PARAMETER", "message": str(e)},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error_code": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except DomainError as e:
        raise HTTPException(
            status_code=e.http_status,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except Exception as e:
        logger.exception(f"Unhandled error in get_upload_url: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
        )


# ---------------------------------------------------------------------------
# Upload Completion / Cancellation
# ---------------------------------------------------------------------------


@router.post(
    "/{tenant}/{session_id}/files/upload-url/{transfer_id}/complete",
    response_model=ApiResponse[SessionCompleteUploadResponse],
    summary="Complete an upload (SINGLE or MULTIPART)",
)
async def complete_upload(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    session_id: Annotated[str, Path(description="Session identifier")],
    transfer_id: Annotated[
        str, Path(description="Transfer ID from upload-url response")
    ],
    dispatcher: SessionFileSharingDispatcher = Depends(
        _get_session_file_sharing_dispatcher
    ),
) -> ApiResponse[SessionCompleteUploadResponse]:
    """Complete a previously initiated Session upload.

    For SINGLE uploads: verifies the file exists in OSS staging.
    For MULTIPART uploads: assembles uploaded parts into the final file.
    Transitions the ticket directly to DONE on success.
    """
    logger.info(
        f"complete_upload: tenant={tenant}, session_id={session_id}, "
        f"transfer_id={transfer_id}"
    )

    try:
        result = await dispatcher.dispatch_complete_upload(
            transfer_id=transfer_id,
            tenant=tenant,
            session_id=session_id,
        )
        return ApiResponse(data=result)

    except TransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except StagingObjectNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": e.error_code,
                "message": str(e),
                "transfer_id": transfer_id,
            },
        )
    except TransferStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error_code": "INVALID_TRANSITION", "message": str(e)},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error_code": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except DomainError as e:
        raise HTTPException(
            status_code=e.http_status,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except Exception as e:
        logger.exception(f"Unhandled error in complete_upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
        )


@router.delete(
    "/{tenant}/{session_id}/files/upload-url/{transfer_id}",
    response_model=ApiResponse[SessionCancelUploadResponse],
    summary="Cancel an upload and abort any in-progress multipart session",
)
async def cancel_upload(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    session_id: Annotated[str, Path(description="Session identifier")],
    transfer_id: Annotated[
        str, Path(description="Transfer ID from upload-url response")
    ],
    dispatcher: SessionFileSharingDispatcher = Depends(
        _get_session_file_sharing_dispatcher
    ),
) -> ApiResponse[SessionCancelUploadResponse]:
    """Cancel an in-progress Session upload.

    If the upload is multipart, the OSS multipart session is aborted.
    The ticket transitions to CANCELLED terminal state.
    """
    logger.info(
        f"cancel_upload: tenant={tenant}, session_id={session_id}, "
        f"transfer_id={transfer_id}"
    )

    try:
        result = await dispatcher.dispatch_cancel_upload(
            transfer_id=transfer_id,
            tenant=tenant,
            session_id=session_id,
        )
        return ApiResponse(data=result)

    except TransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except TransferStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error_code": "INVALID_TRANSITION", "message": str(e)},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error_code": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except DomainError as e:
        raise HTTPException(
            status_code=e.http_status,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except Exception as e:
        logger.exception(f"Unhandled error in cancel_upload: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
        )


# ---------------------------------------------------------------------------
# Share Link
# ---------------------------------------------------------------------------


@router.post(
    "/{tenant}/{session_id}/files/transfers/{transfer_id}/share-link",
    response_model=ApiResponse[SessionShareLinkResponse],
    summary="Generate a shareable download link for a completed Session transfer",
)
async def generate_share_link(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    session_id: Annotated[str, Path(description="Session identifier")],
    transfer_id: Annotated[
        str, Path(description="Transfer ID from upload-url response")
    ],
    request: SessionShareLinkRequest,
    dispatcher: SessionFileSharingDispatcher = Depends(
        _get_session_file_sharing_dispatcher
    ),
) -> ApiResponse[SessionShareLinkResponse]:
    """Generate a shareable download link for a completed Session file transfer.

    Only tickets in DONE status are eligible.  The share URL is a
    pre-signed OSS GET URL with bounded expiry.  Session share links are
    synchronous — no ticket is created (unlike Bot's async download flow).
    """
    logger.info(
        f"generate_share_link: tenant={tenant}, session_id={session_id}, "
        f"transfer_id={transfer_id}, expire_seconds={request.expire_seconds}"
    )

    try:
        result = await dispatcher.dispatch_get_share_link(
            transfer_id=transfer_id,
            tenant=tenant,
            session_id=session_id,
            expire_seconds=request.expire_seconds,
            show=request.show,
            operator=request.operator,
        )
        return ApiResponse(data=result)

    except SourceTransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": e.error_code,
                "message": str(e),
                "transfer_id": e.transfer_id,
            },
        )
    except SourceTransferNotReadyError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": e.error_code,
                "message": str(e),
                "transfer_id": e.transfer_id,
                "current_status": e.current_status,
            },
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error_code": "INVALID_TRANSITION", "message": str(e)},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error_code": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except DomainError as e:
        raise HTTPException(
            status_code=e.http_status,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except Exception as e:
        logger.exception(f"Unhandled error in generate_share_link: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
        )


# ---------------------------------------------------------------------------
# Transfer Status Query
# ---------------------------------------------------------------------------


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
            detail={"error_code": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except DomainError as e:
        raise HTTPException(
            status_code=e.http_status,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except Exception as e:
        logger.exception(f"Unhandled error in get_transfer_status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
        )


# ---------------------------------------------------------------------------
# Transfer Deletion
# ---------------------------------------------------------------------------


@router.delete(
    "/{tenant}/{session_id}/transfers/{transfer_id}",
    response_model=ApiResponse[SessionDeleteTransferResponse],
    summary="Delete a Session transfer ticket and its OSS staging object",
)
async def delete_transfer(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    session_id: Annotated[str, Path(description="Session identifier")],
    transfer_id: Annotated[str, Path(description="Transfer ID to delete")],
    dispatcher: SessionFileSharingDispatcher = Depends(
        _get_session_file_sharing_dispatcher
    ),
) -> ApiResponse[SessionDeleteTransferResponse]:
    """Delete a Session transfer ticket and its associated OSS staging object.

    Only tickets in a terminal state (DONE/FAILED/CANCELLED/DELETED)
    can be deleted.  The ticket transitions to DELETED on success.
    Already-DELETED tickets are handled idempotently.
    """
    logger.info(
        f"delete_transfer: tenant={tenant}, session_id={session_id}, "
        f"transfer_id={transfer_id}"
    )

    try:
        result = await dispatcher.dispatch_delete_transfer(
            transfer_id=transfer_id,
            tenant=tenant,
            session_id=session_id,
        )
        return ApiResponse(data=result)

    except TransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except TransferNotTerminalError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": e.error_code,
                "message": str(e),
                "transfer_id": e.transfer_id,
            },
        )
    except TransferStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error_code": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except DomainError as e:
        raise HTTPException(
            status_code=e.http_status,
            detail={"error_code": e.error_code, "message": str(e)},
        )
    except Exception as e:
        logger.exception(f"Unhandled error in delete_transfer: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
        )
