"""Pydantic models for file transfer HTTP API requests and responses.

Request/response models for upload-url, download-url, and transfer-status query
endpoints.  Also includes the TransferNotFoundError domain exception used by the
Dispatcher and Router layers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GetUploadUrlRequest(BaseModel):
    """Request model for requesting an upload pre-signed URL on a bot device.

    The caller provides device_path (optional — None means retention mode,
    file stays in OSS only) and optional staging_subdir for OSS path
    construction.  If filename is not provided, it is extracted from
    device_path when present.

    file_size enables SINGLE/MULTIPART routing: 0 or unset defaults to SINGLE.
    """

    device_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="Device-side target file path for the upload; None for retention mode (file stays in OSS only)",
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
    file_size: int = Field(
        default=0,
        ge=0,
        description="File size in bytes for SINGLE/MULTIPART routing. 0 or unset defaults to SINGLE.",
    )
    part_size: int | None = Field(
        default=None,
        ge=1048576,
        description="Custom part size in bytes for multipart, defaults to 10MB if file_size >= threshold.",
    )

    model_config = {"from_attributes": True}


class GetUploadUrlResponse(BaseModel):
    """Response for a successful upload URL request.

    Returns the pre-signed PUT URL plus transfer_id for subsequent polling.
    For MULTIPART uploads, returns parts list and upload_session_id instead
    of a single upload_url.
    """

    upload_url: str | None = None
    transfer_id: str
    expires_at: str
    type: str = Field(
        default="SINGLE",
        description="Upload mode: SINGLE or MULTIPART.",
    )
    upload_session_id: str | None = Field(
        default=None,
        description="OSS multipart upload session ID (only for MULTIPART).",
    )
    part_size: int | None = Field(
        default=None,
        description="Part size in bytes (only for MULTIPART).",
    )
    part_count: int | None = Field(
        default=None,
        description="Total number of parts (only for MULTIPART).",
    )
    parts: list[dict] | None = Field(
        default=None,
        description="List of part objects [{part_number, upload_url, expires_at}] (only for MULTIPART).",
    )

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
    - expires_at: intentionally null for transfer queries; OSS presigned URLs
      embed their own expiry via the Expires query parameter
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


class CompleteUploadResponse(BaseModel):
    """Response for the upload completion endpoint.

    Returns the transfer_id and resulting status after validation.
    """

    transfer_id: str
    status: str

    model_config = {"from_attributes": True}


class CancelUploadResponse(BaseModel):
    """Response for the upload cancellation endpoint.

    Returns the transfer_id and new status (CANCELLED).
    """

    transfer_id: str
    status: str

    model_config = {"from_attributes": True}


class ShareLinkRequest(BaseModel):
    """Request model for generating a shareable download link.

    expire_seconds defaults to 86400 (24 hours).
    """

    expire_seconds: int = Field(
        default=86400,
        ge=60,
        le=604800,
        description="Share link validity in seconds (60–604800, default 86400)",
    )

    model_config = {"from_attributes": True}


class ShareLinkResponse(BaseModel):
    """Response for a generated share link.

    transfer_id allows callers to correlate the share-link response with
    the originating transfer without additional lookups.
    """

    share_url: str
    transfer_id: str
    expires_at: str

    model_config = {"from_attributes": True}


class StagingListResponse(BaseModel):
    """Response for staging area object listing.

    Returns flat list of objects with marker-based pagination.
    """

    prefix: str
    items: list[dict]
    truncated: bool
    next_marker: str | None = None

    model_config = {"from_attributes": True}


class StagingDeleteResponse(BaseModel):
    """Response for staging object deletion.

    Returns the deleted key, associated transfer_id, and status transition.
    """

    deleted_key: str
    transfer_id: str
    previous_status: str
    new_status: str

    model_config = {"from_attributes": True}


class TransferNotFoundError(Exception):
    """Raised when a transfer ticket is not found by transfer_id."""

    pass
