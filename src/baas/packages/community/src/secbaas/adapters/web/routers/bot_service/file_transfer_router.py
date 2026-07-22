"""Bot File Transfer REST API routes.

Provides endpoints for upload URL generation and download initiation
for bot file transfers.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from secbaas.api import ApiResponse
from secbaas.api.bot_runtime import (
    BotFileTransferDispatcher,
    BotNotFoundError,
    CancelUploadResponse,
    CompleteUploadResponse,
    DeleteTransferResponse,
    GetDownloadUrlRequest,
    GetDownloadUrlResponse,
    GetUploadUrlRequest,
    GetUploadUrlResponse,
    NoActiveDevicesError,
    NoDevicesFoundError,
    OssObjectNotFoundError,
    ShareLinkRequest,
    ShareLinkResponse,
    TransferNotFoundError,
    TransferNotTerminalError,
    TransferStateConflictError,
)
from secbaas.api.device_manage import DeviceFacadeException
from secbaas.bootstrap import ApplicationContainer
from secbaas.logger import get_logger

logger = get_logger("router")

router = APIRouter(prefix="/api/v1/bots", tags=["Bot文件传输"])


@router.post(
    "/{tenant}/{bot_uuid}/files/upload-url",
    response_model=ApiResponse[GetUploadUrlResponse],
    summary="Get a presigned upload URL for file upload",
)
@inject
async def get_upload_url(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    request: GetUploadUrlRequest,
    device_affinity: Annotated[
        str | None,
        Query(
            description="Device affinity key for sticky device selection. "
            "Hidden from schema as this is an internal routing hint, "
            "not a user-facing parameter.",
            include_in_schema=False,
        ),
    ] = None,
    dispatcher: BotFileTransferDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_file_transfer_dispatcher]
    ),
) -> ApiResponse[GetUploadUrlResponse]:
    """Get a presigned upload URL for uploading a file to the bot's device via OSS.

    Returns upload_url (PUT to OSS), transfer_id, and expires_at.
    The Poller will later detect the uploaded file and trigger device download.
    """
    logger.info(
        f"get_upload_url: bot_uuid={bot_uuid}, tenant={tenant}, "
        f"device_path={request.device_path}, filename={request.filename}"
    )

    try:
        result = await dispatcher.dispatch_get_upload_url(
            bot_uuid=bot_uuid,
            tenant=tenant,
            device_path=request.device_path,
            filename=request.filename,
            expire_seconds=request.expire_seconds,
            staging_subdir=request.staging_subdir,
            device_affinity=device_affinity,
            file_size=request.file_size,
            part_size=request.part_size,
            operator=request.operator,
        )
        return ApiResponse(data=result)

    except BotNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "BOT_NOT_FOUND", "message": str(e), "bot_uuid": bot_uuid},
        )
    except NoDevicesFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "NO_DEVICES_FOUND",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
    except NoActiveDevicesError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "NO_ACTIVE_DEVICES",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "NOT_IMPLEMENTED",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
    except DeviceFacadeException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": e.original_error.code.value
                if e.original_error
                else "FACADE_ERROR",
                "message": str(e),
                "context": {
                    "operation": e.operation,
                    "platform_type": e.platform_type,
                    "paas_device_id": e.paas_device_id,
                },
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "INTERNAL_ERROR", "message": str(e), "bot_uuid": bot_uuid},
        )


@router.post(
    "/{tenant}/{bot_uuid}/files/download-url",
    response_model=ApiResponse[GetDownloadUrlResponse],
    summary="Initiate device file upload to OSS and get a transfer ID for polling",
)
@inject
async def get_download_url(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    request: GetDownloadUrlRequest,
    device_affinity: Annotated[
        str | None,
        Query(
            description="Device affinity key for sticky device selection. "
            "Hidden from schema as this is an internal routing hint, "
            "not a user-facing parameter.",
            include_in_schema=False,
        ),
    ] = None,
    dispatcher: BotFileTransferDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_file_transfer_dispatcher]
    ),
) -> ApiResponse[GetDownloadUrlResponse]:
    """Triggers the device to push a file to OSS and returns a transfer_id.

    The Poller will detect the uploaded file and make a download URL
    available via the query endpoint.
    """
    logger.info(
        f"get_download_url: bot_uuid={bot_uuid}, tenant={tenant}, "
        f"device_path={request.device_path}"
    )

    try:
        result = await dispatcher.dispatch_get_download_url(
            bot_uuid=bot_uuid,
            tenant=tenant,
            device_path=request.device_path,
            expire_seconds=request.expire_seconds,
            device_affinity=device_affinity,
            operator=request.operator,
        )
        return ApiResponse(data=result)

    except BotNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "BOT_NOT_FOUND", "message": str(e), "bot_uuid": bot_uuid},
        )
    except NoDevicesFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "NO_DEVICES_FOUND",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
    except NoActiveDevicesError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "NO_ACTIVE_DEVICES",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "NOT_IMPLEMENTED",
                "message": str(e),
                "bot_uuid": bot_uuid,
            },
        )
    except DeviceFacadeException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": e.original_error.code.value
                if e.original_error
                else "FACADE_ERROR",
                "message": str(e),
                "context": {
                    "operation": e.operation,
                    "platform_type": e.platform_type,
                    "paas_device_id": e.paas_device_id,
                },
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "INTERNAL_ERROR", "message": str(e), "bot_uuid": bot_uuid},
        )


# ── v1.5 endpoints ─────────────────────────────────────────────────────


@router.post(
    "/{tenant}/{bot_uuid}/files/upload-url/{transfer_id}/complete",
    response_model=ApiResponse[CompleteUploadResponse],
    summary="Complete an upload (SINGLE or MULTIPART)",
)
@inject
async def complete_upload(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    transfer_id: Annotated[
        str, Path(description="Transfer ID from upload-url response")
    ],
    dispatcher: BotFileTransferDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_file_transfer_dispatcher]
    ),
) -> ApiResponse[CompleteUploadResponse]:
    """Complete a previously initiated upload.

    For SINGLE uploads: verifies the file exists in OSS staging.
    For MULTIPART uploads: assembles uploaded parts into the final file.
    Transitions the ticket to UPLOAD_COMPLETED on success.
    """
    logger.info(
        f"complete_upload: tenant={tenant}, bot_uuid={bot_uuid}, "
        f"transfer_id={transfer_id}"
    )

    try:
        result = await dispatcher.dispatch_complete_upload(
            transfer_id=transfer_id,
            tenant=tenant,
        )
        return ApiResponse(data=result)

    except TransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except OssObjectNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": e.error_code,
                "message": str(e),
                "transfer_id": transfer_id,
            },
        )
    except TransferStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "TRANSFER_STATE_CONFLICT", "message": str(e)},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "INTERNAL_ERROR", "message": str(e)},
        )


@router.delete(
    "/{tenant}/{bot_uuid}/files/upload-url/{transfer_id}",
    response_model=ApiResponse[CancelUploadResponse],
    summary="Cancel an upload and abort any in-progress multipart session",
)
@inject
async def cancel_upload(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    transfer_id: Annotated[
        str, Path(description="Transfer ID from upload-url response")
    ],
    dispatcher: BotFileTransferDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_file_transfer_dispatcher]
    ),
) -> ApiResponse[CancelUploadResponse]:
    """Cancel an in-progress upload.

    If the upload is multipart, the OSS multipart session is aborted.
    The ticket transitions to CANCELLED terminal state.
    """
    logger.info(
        f"cancel_upload: tenant={tenant}, bot_uuid={bot_uuid}, "
        f"transfer_id={transfer_id}"
    )

    try:
        result = await dispatcher.dispatch_cancel_upload(
            transfer_id=transfer_id,
            tenant=tenant,
        )
        return ApiResponse(data=result)

    except TransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except TransferStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "TRANSFER_STATE_CONFLICT", "message": str(e)},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "INTERNAL_ERROR", "message": str(e)},
        )


@router.delete(
    "/{tenant}/{bot_uuid}/files/transfers/{transfer_id}",
    response_model=ApiResponse[DeleteTransferResponse],
    summary="Delete a completed transfer and its OSS staging object",
)
@inject
async def delete_transfer(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    transfer_id: Annotated[
        str, Path(description="Transfer ID to delete")
    ],
    dispatcher: BotFileTransferDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_file_transfer_dispatcher]
    ),
) -> ApiResponse[DeleteTransferResponse]:
    """Delete a transfer ticket and its associated OSS staging object.

    Only tickets in a terminal state (DONE/FAILED/CANCELLED/DELETED)
    can be deleted.  The ticket transitions to DELETED on success.
    Already-DELETED tickets are handled idempotently.
    """
    logger.info(
        f"delete_transfer: tenant={tenant}, bot_uuid={bot_uuid}, "
        f"transfer_id={transfer_id}"
    )

    try:
        result = await dispatcher.dispatch_delete_transfer(
            transfer_id=transfer_id,
            tenant=tenant,
        )
        return ApiResponse(data=result)

    except TransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except TransferNotTerminalError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": e.error_code,
                "message": str(e),
                "transfer_id": e.transfer_id,
            },
        )
    except TransferStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": e.error_code,
                "message": str(e),
                "transfer_id": getattr(e, "transfer_id", None),
            },
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "INTERNAL_ERROR", "message": str(e)},
        )


@router.post(
    "/{tenant}/{bot_uuid}/files/transfers/{transfer_id}/share-link",
    response_model=ApiResponse[ShareLinkResponse],
    summary="Generate a shareable download link for a completed transfer",
)
@inject
async def generate_share_link(
    tenant: Annotated[str, Path(description="Tenant for isolation")],
    bot_uuid: Annotated[str, Path(description="Bot UUID")],
    transfer_id: Annotated[
        str, Path(description="Transfer ID from upload-url response")
    ],
    request: ShareLinkRequest,
    dispatcher: BotFileTransferDispatcher = Depends(
        Provide[ApplicationContainer.services.bot_file_transfer_dispatcher]
    ),
) -> ApiResponse[ShareLinkResponse]:
    """Generate a shareable download link for a completed file transfer.

    Only tickets in DONE status are eligible.  The share URL is a
    pre-signed OSS GET URL with bounded expiry (default 24h, max 7d).
    """
    logger.info(
        f"generate_share_link: tenant={tenant}, bot_uuid={bot_uuid}, "
        f"transfer_id={transfer_id}, expire_seconds={request.expire_seconds}"
    )

    try:
        result = await dispatcher.dispatch_generate_share_link(
            transfer_id=transfer_id,
            expire_seconds=request.expire_seconds,
            tenant=tenant,
        )
        return ApiResponse(data=result)

    except TransferNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "TRANSFER_NOT_FOUND", "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "INVALID_TRANSITION", "message": str(e)},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": "NOT_IMPLEMENTED", "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "INTERNAL_ERROR", "message": str(e)},
        )


