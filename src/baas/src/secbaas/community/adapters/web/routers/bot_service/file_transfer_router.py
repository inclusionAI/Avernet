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
    GetDownloadUrlRequest,
    GetDownloadUrlResponse,
    GetUploadUrlRequest,
    GetUploadUrlResponse,
    NoActiveDevicesError,
    NoDevicesFoundError,
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
        Query(description="Device affinity key", include_in_schema=False),
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
            detail={"error": "NO_DEVICES_FOUND", "message": str(e), "bot_uuid": bot_uuid},
        )
    except NoActiveDevicesError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "NO_ACTIVE_DEVICES", "message": str(e), "bot_uuid": bot_uuid},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": "NOT_IMPLEMENTED", "message": str(e), "bot_uuid": bot_uuid},
        )
    except DeviceFacadeException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": e.original_error.code.value if e.original_error else "FACADE_ERROR",
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
        Query(description="Device affinity key", include_in_schema=False),
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
            detail={"error": "NO_DEVICES_FOUND", "message": str(e), "bot_uuid": bot_uuid},
        )
    except NoActiveDevicesError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "NO_ACTIVE_DEVICES", "message": str(e), "bot_uuid": bot_uuid},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"error": "NOT_IMPLEMENTED", "message": str(e), "bot_uuid": bot_uuid},
        )
    except DeviceFacadeException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": e.original_error.code.value if e.original_error else "FACADE_ERROR",
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