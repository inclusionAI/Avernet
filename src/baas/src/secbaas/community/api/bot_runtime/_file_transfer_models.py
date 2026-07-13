"""Pydantic models for file transfer HTTP API requests and responses.

Request/response models for upload-url, download-url, and transfer-status query
endpoints.  Also includes the TransferNotFoundError domain exception used by the
Dispatcher and Router layers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GetUploadUrlRequest(BaseModel):
    """Request model for requesting an upload pre-signed URL on a bot device.

    The caller provides device_path (required) and optional staging_subdir
    for OSS path construction.  If filename is not provided, it is extracted
    from device_path.
    """

    device_path: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Device-side target file path for the upload",
    )
    filename: str | None = Field(
        default=None,
        description="File name for OSS staging; extracted from device_path if omitted",
    )
    expire_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Pre-signed URL validity duration in seconds (60–86400, default 3600)",
    )
    staging_subdir: str | None = Field(
        default=None,
        description="Optional subdirectory under the file-transfers/ OSS prefix",
    )

    model_config = {"from_attributes": True}


class GetUploadUrlResponse(BaseModel):
    """Response for a successful upload URL request.

    Returns the pre-signed PUT URL plus transfer_id for subsequent polling.
    """

    upload_url: str
    transfer_id: str
    expires_at: str

    model_config = {"from_attributes": True}


class GetDownloadUrlRequest(BaseModel):
    """Request model for requesting a download from a bot device.

    The device uploads to OSS and the caller polls for the download URL.
    filename is ALWAYS extracted from device_path (D-06).
    """

    device_path: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Absolute path of the file on the device to download",
    )
    expire_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Pre-signed URL validity duration in seconds (60–86400, default 3600)",
    )

    model_config = {"from_attributes": True}


class GetDownloadUrlResponse(BaseModel):
    """Response for a successful download URL request.

    Does NOT include download_url — the download direction uses an async
    ticket model (D-05): the caller polls via GetTransferStatusResponse
    when the file arrives.
    """

    transfer_id: str
    expires_at: str

    model_config = {"from_attributes": True}


class GetTransferStatusResponse(BaseModel):
    """Response for the transfer status query endpoint.

    Maps from TicketRecord.  Conditional fields:
    - download_url: only present when status == DONE
    - upload_url: only present when status == CREATED (resume support)
    - expires_at: only present when either URL is included
    - error_message: only present when status == FAILED
    """

    transfer_id: str
    status: str
    direction: str
    filename: str
    device_path: str | None
    download_url: str | None = None
    upload_url: str | None = None
    expires_at: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class TransferNotFoundError(Exception):
    """Raised when a transfer ticket is not found by transfer_id."""

    pass