"""Session File Sharing Dispatcher protocol.

The Dispatcher is the business-logic layer between the HTTP Router (Phase 78)
and the SPI / Repository backends.  It encapsulates upload/download orchestration,
staging path construction, pre-signed URL generation, ticket lifecycle management,
and ownership validation — without any HTTP concern or DI wiring.

Session File Sharing is a separate domain from Bot Device File Transfer:
no ``device_path``, no ``direction``, no ``paas_device_id``, and no
``UPLOAD_COMPLETED`` / ``PULLING`` intermediate ticket states.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from secbaas.community.api.session_file_sharing._models import (
    SessionCancelUploadResponse,
    SessionCompleteUploadResponse,
    SessionDeleteTransferResponse,
    SessionGetTransferStatusResponse,
    SessionGetUploadUrlResponse,
    SessionShareLinkResponse,
)


@runtime_checkable
class SessionFileSharingDispatcher(Protocol):
    """Protocol for Session File Sharing dispatch operations.

    Six methods cover the full Session transfer lifecycle:
    get-upload-url → complete/cancel → share-link / status / delete.

    All methods are async.  Implementations inject ``FileTransferBackend``
    (for OSS operations) and ``SessionTicketRepository`` (for ticket
    persistence) via the constructor.
    """

    async def dispatch_get_upload_url(
        self,
        tenant: str,
        session_id: str,
        filename: str,
        expire_seconds: int = 3600,
        staging_subdir: str | None = None,
        file_size: int = 0,
        part_size: int | None = None,
        operator: str | None = None,
    ) -> SessionGetUploadUrlResponse:
        """Generate a pre-signed upload URL for a Session file.

        Constructs a Session-scoped staging path (via
        ``build_session_staging_path``) and routes to SINGLE or MULTIPART
        based on ``file_size`` vs the 100MB threshold.  Creates a ticket
        **after** OSS success to keep DB/OSS consistent.

        Args:
            tenant: Tenant identifier for scoping.
            session_id: Owning session identifier.
            filename: File name for the upload.
            expire_seconds: Pre-signed URL validity in seconds.
            staging_subdir: Optional subdirectory grouping.
            file_size: File size in bytes (0 triggers SINGLE mode).
            part_size: Custom multipart part size (None → use default 10MB).
            operator: Identifier of the user or system initiating the upload.
        """
        ...

    async def dispatch_complete_upload(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> SessionCompleteUploadResponse:
        """Validate and finalize a SINGLE or MULTIPART Session upload.

        SINGLE: checks OSS object existence → status → DONE.
        MULTIPART: lists uploaded parts, assembles them → status → DONE.

        Session goes directly to DONE (no ``UPLOAD_COMPLETED`` /
        ``PULLING`` intermediate states).  DONE tickets are idempotent
        on re-call.
        """
        ...

    async def dispatch_cancel_upload(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> SessionCancelUploadResponse:
        """Cancel an in-progress Session upload.

        Aborts the OSS multipart session (if any) and transitions the
        ticket to CANCELLED.  Already-terminal tickets return idempotent
        success.
        """
        ...

    async def dispatch_get_share_link(
        self,
        transfer_id: str,
        tenant: str,
        session_id: str,
        expire_seconds: int = 3600,
        show: bool = False,
        operator: str | None = None,
    ) -> SessionShareLinkResponse:
        """Generate a shareable download link for a completed Session upload.

        Only DONE tickets are eligible.  Validates tenant + session_id
        ownership before generating the pre-signed GET URL.  Synchronous
        — no ticket is created (unlike Bot's async download flow).
        ``show=False`` adds ``Content-Disposition: attachment`` to force
        download; ``show=True`` produces an inline/preview URL.
        """
        ...

    async def dispatch_get_transfer_status(
        self,
        transfer_id: str,
        tenant: str | None = None,
        session_id: str | None = None,
    ) -> SessionGetTransferStatusResponse:
        """Query a Session transfer ticket by transfer_id.

        Optionally scoped to tenant and session_id.  Ownership mismatch
        raises ``TransferNotFoundError`` (404) — does not reveal whether
        the transfer_id exists for another session.
        """
        ...

    async def dispatch_delete_transfer(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> SessionDeleteTransferResponse:
        """Delete a Session transfer ticket and its OSS staging object.

        Only terminal-state tickets (DONE / FAILED / CANCELLED / DELETED)
        can be deleted.  Already-DELETED tickets return idempotent success.
        OSS deletion tolerates ``NoSuchKey`` — lifecycle policies may have
        already cleaned up the object.
        """
        ...
