"""Pydantic models for the Session File Sharing HTTP API.

All models are independent (per D-05 — no subclassing of Bot Pydantic models).
Session File Sharing has no ``device_path`` or ``direction`` concepts because
every upload originates from the caller and OSS is the terminal store.

Field specifications follow ``docs/ocb-session-file-sharing-api.md``.  All
optional fields use ``Field(default=None)`` (per D-08 / project CLAUDE.md)
— never bare ``T | None = None``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ============================================================================
# Upload URL models
# ============================================================================


class SessionGetUploadUrlRequest(BaseModel):
    """Request to obtain an OSS pre-signed upload URL for a Session file.

    Unlike the Bot equivalent, ``filename`` is **required** because Session
    transfers have no ``device_path`` from which to extract it.
    """

    filename: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="File name for the upload (required — no device_path in Session context)",
    )
    expire_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Pre-signed URL validity in seconds (60–86400, default 3600)",
    )
    staging_subdir: str | None = Field(
        default=None,
        max_length=256,
        description="Optional subdirectory grouping for file organisation",
    )
    file_size: int = Field(
        default=0,
        ge=0,
        description="File size in bytes; 0 or unset defaults to SINGLE mode",
    )
    part_size: int | None = Field(
        default=None,
        ge=1048576,
        description="Custom part size for multipart (min 1 MiB)",
    )
    operator: str = Field(
        default="unknown",
        max_length=256,
        description="Identifier of the user or system initiating the upload",
    )
    content_type: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional MIME type for the uploaded file. When set, the presigned "
            "PUT URL's signature includes Content-Type, and OSS will reject PUT "
            "requests with a mismatched Content-Type header (403). When None "
            "(default), no Content-Type constraint is applied."
        ),
    )

    model_config = {"from_attributes": True}


class SessionGetUploadUrlResponse(BaseModel):
    """Response for a successful upload URL request.

    ``upload_url`` is None for MULTIPART mode (each part has its own URL).
    ``expires_at`` is None for MULTIPART mode (each part has its own expiry).
    """

    upload_url: str | None = Field(default=None)
    transfer_id: str
    http_method: str = Field(
        default="PUT",
        description="HTTP method for the upload URL (always PUT)",
    )
    expires_at: str | None = Field(
        default=None,
        description="Upload URL expiry (ISO 8601); None in MULTIPART mode",
    )
    type: str = Field(
        default="SINGLE",
        description="Upload mode: SINGLE or MULTIPART",
    )
    upload_session_id: str | None = Field(
        default=None,
        description="OSS multipart upload session ID (MULTIPART only)",
    )
    part_size: int | None = Field(
        default=None,
        description="Part size in bytes (MULTIPART only)",
    )
    part_count: int | None = Field(
        default=None,
        description="Total number of parts (MULTIPART only)",
    )
    parts: list[dict] | None = Field(
        default=None,
        description="List of part objects [{part_number, upload_url, http_method, expires_at}] (MULTIPART only)",
    )

    model_config = {"from_attributes": True}


# ============================================================================
# Upload completion / cancellation models
# ============================================================================


class SessionCompleteUploadResponse(BaseModel):
    """Response after completing or verifying an upload."""

    transfer_id: str
    status: str

    model_config = {"from_attributes": True}


class SessionCancelUploadResponse(BaseModel):
    """Response after cancelling an in-progress upload."""

    transfer_id: str
    status: str

    model_config = {"from_attributes": True}


# ============================================================================
# Share-link models (synchronous — no ticket created, per D-06)
# ============================================================================


class SessionShareLinkRequest(BaseModel):
    """Request to generate a shareable download link.

    Session share links are **synchronous** (no ticket / no polling).
    ``expire_seconds`` defaults to 3600 (Session API spec), NOT 86400 (Bot default).
    """

    expire_seconds: int = Field(
        default=3600,
        ge=60,
        le=604800,
        description="Share link validity in seconds (60–604800, default 3600 for Session)",
    )
    show: bool = Field(
        default=False,
        description="False → Content-Disposition: attachment (download); True → inline (preview)",
    )
    operator: str = Field(
        default="unknown",
        max_length=256,
        description="Identifier of the user requesting the share link",
    )

    model_config = {"from_attributes": True}


class SessionShareLinkResponse(BaseModel):
    """Response containing a generated share link.

    ``share_url`` is an OSS pre-signed GET URL with optional
    ``response-content-disposition`` query parameter driven by ``show``.
    """

    share_url: str
    transfer_id: str
    expires_at: str

    model_config = {"from_attributes": True}


# ============================================================================
# Transfer status / deletion models
# ============================================================================


class SessionGetTransferStatusResponse(BaseModel):
    """Response for the transfer status query endpoint.

    Session-specific (no ``direction`` or ``device_path``).  Contains
    ``session_id`` to identify the owning session.
    """

    transfer_id: str
    status: str
    filename: str
    session_id: str
    error_message: str | None = Field(
        default=None,
        description="Only present when status == FAILED",
    )
    created_at: str
    updated_at: str
    operator: str

    model_config = {"from_attributes": True}


class SessionDeleteTransferResponse(BaseModel):
    """Response after deleting a transfer and its OSS staging object.

    Returns the status before and after deletion for audit visibility.
    """

    transfer_id: str
    previous_status: str
    new_status: str

    model_config = {"from_attributes": True}
