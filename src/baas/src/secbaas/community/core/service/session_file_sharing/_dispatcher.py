"""DefaultSessionFileSharingDispatcher — Session file upload/download orchestration.

Implements the ``SessionFileSharingDispatcher`` protocol with all six
dispatch methods.  Encapsulates upload, completion, cancellation, share-link
generation, status query, and deletion for Session File Sharing transfers.

Session File Sharing is a separate domain from Bot Device File Transfer:
no ``device_path``, no ``direction``, no ``paas_device_id``, and no
``UPLOAD_COMPLETED`` / ``PULLING`` intermediate ticket states.  Uploads
go directly to DONE on completion.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from secbaas.community.api.session_file_sharing import (
    SessionCancelUploadResponse,
    SessionCompleteUploadResponse,
    SessionDeleteTransferResponse,
    SessionFileSharingDispatcher,
    SessionGetTransferStatusResponse,
    SessionGetUploadUrlResponse,
    SessionShareLinkResponse,
    SourceTransferNotFoundError,
    SourceTransferNotReadyError,
    TransferNotFoundError,
    TransferNotTerminalError,
    TransferStateConflictError,
)
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.core.repository.session_file_ticket import (
        SessionTicketRepository,
    )
    from secbaas.community.spi.file_transfer import FileTransferBackend

logger = get_logger("core-service")


class DefaultSessionFileSharingDispatcher(SessionFileSharingDispatcher):
    """File transfer dispatcher for Session File Sharing.

    Implements ``SessionFileSharingDispatcher`` protocol.  Injects
    ``FileTransferBackend`` (for OSS operations) and
    ``SessionTicketRepository`` (for ticket persistence) via the
    constructor — no bot/device resolution needed.

    Six methods cover the full Session transfer lifecycle:
    get-upload-url → complete/cancel → share-link / status / delete.
    """

    # v1.5 multipart routing thresholds
    MULTIPART_THRESHOLD = 104_857_600  # 100MB
    DEFAULT_PART_SIZE = 10_485_760  # 10MB

    def __init__(
        self,
        file_transfer_backend: FileTransferBackend,
        ticket_repo: SessionTicketRepository,
    ):
        self._file_transfer_backend = file_transfer_backend
        self._ticket_repo = ticket_repo

    # ------------------------------------------------------------------
    # dispatch_get_upload_url
    # ------------------------------------------------------------------

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
        """Orchestrate upload URL generation for a Session file.

        Steps:
        1. Normalize operator (empty/None → "unknown")
        2. Validate staging_subdir (reject "..", strip "/")
        3. Validate file_size (must be ≥ 0)
        4. Generate transfer_id
        5. Construct Session-scoped staging path via
           ``build_session_staging_path``
        6. Route SINGLE or MULTIPART based on file_size vs 100MB threshold
        7. Create ticket AFTER OSS success (DB/OSS consistency)
        8. Return response
        """
        logger.info(
            "Dispatching Session upload URL: tenant=%s, session_id=%s, "
            "filename=%s, file_size=%d",
            tenant,
            session_id,
            filename,
            file_size,
        )

        # Normalize empty/None operator to "unknown" before any DB write
        if not operator or not operator.strip():
            operator = "unknown"

        # Validate staging_subdir — reject path traversal
        if staging_subdir is not None:
            if ".." in staging_subdir:
                raise ValueError("staging_subdir contains invalid path traversal")
            staging_subdir = staging_subdir.strip("/")

        # Validate file_size
        if file_size < 0:
            raise ValueError(f"file_size must be non-negative, got {file_size}")

        transfer_id = uuid.uuid4().hex

        # Construct Session-scoped staging path via backend (D-01, CORE-05)
        staging_path = self._file_transfer_backend.build_session_staging_path(
            tenant=tenant,
            session_id=session_id,
            transfer_id=transfer_id,
            filename=filename,
            subdir=staging_subdir,
        )

        expires_at = (
            datetime.now(UTC) + timedelta(seconds=expire_seconds)
        ).isoformat()

        # SINGLE / MULTIPART routing
        if file_size >= self.MULTIPART_THRESHOLD:
            # ---- MULTIPART path ----
            effective_part_size = part_size if part_size else self.DEFAULT_PART_SIZE
            if effective_part_size <= 0:
                raise ValueError(
                    f"part_size must be positive, got {part_size}"
                )
            part_count = -(-file_size // effective_part_size)  # ceil division

            # OSS limits multipart uploads to 10,000 parts.  Dynamically
            # increase part_size to keep part_count within this bound.
            _max_parts = 10000
            if part_count > _max_parts:
                effective_part_size = -(-file_size // _max_parts)
                part_count = -(-file_size // effective_part_size)
                logger.info(
                    "Adjusted part_size to %d to keep part_count (%d) "
                    "within OSS 10000 limit",
                    effective_part_size,
                    part_count,
                )

            multipart_session = await asyncio.to_thread(
                self._file_transfer_backend.initiate_multipart_upload,
                staging_path,
                expire_seconds,
                part_count,
            )

            logger.info(
                "Multipart upload initiated: transfer_id=%s, session_id=%s, "
                "part_count=%d",
                transfer_id,
                multipart_session.session_id,
                part_count,
            )

            parts_data = [
                {
                    "part_number": p.part_number,
                    "upload_url": p.upload_url,
                    "http_method": "PUT",
                    "expires_at": expires_at,
                }
                for p in multipart_session.parts
            ]

            # Create ticket AFTER OSS success — DB/OSS consistency
            await asyncio.to_thread(
                self._ticket_repo.create_ticket,
                transfer_id=transfer_id,
                tenant=tenant,
                session_id=session_id,
                status="CREATED",
                staging_subdir=staging_subdir,
                filename=filename,
                fileservice_staging_path=staging_path,
                error_message=None,
                multipart_session_id=multipart_session.session_id,
                operator=operator,
            )

            logger.info(
                "Ticket created (MULTIPART): transfer_id=%s", transfer_id
            )

            return SessionGetUploadUrlResponse(
                type="MULTIPART",
                upload_session_id=multipart_session.session_id,
                part_size=effective_part_size,
                part_count=part_count,
                parts=parts_data,
                transfer_id=transfer_id,
            )
        else:
            # ---- SINGLE path ----
            upload_url = await asyncio.to_thread(
                self._file_transfer_backend.generate_upload_url,
                staging_path,
                expire_seconds,
            )

            logger.info(
                "Upload URL generated: transfer_id=%s, staging_path=%s",
                transfer_id,
                staging_path,
            )

            # Create ticket AFTER OSS success
            await asyncio.to_thread(
                self._ticket_repo.create_ticket,
                transfer_id=transfer_id,
                tenant=tenant,
                session_id=session_id,
                status="CREATED",
                staging_subdir=staging_subdir,
                filename=filename,
                fileservice_staging_path=staging_path,
                error_message=None,
                operator=operator,
            )

            logger.info(
                "Ticket created (SINGLE): transfer_id=%s", transfer_id
            )

            return SessionGetUploadUrlResponse(
                upload_url=upload_url,
                transfer_id=transfer_id,
                http_method="PUT",
                expires_at=expires_at,
                type="SINGLE",
            )

    # ------------------------------------------------------------------
    # dispatch_complete_upload
    # ------------------------------------------------------------------

    async def dispatch_complete_upload(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> SessionCompleteUploadResponse:
        """Complete an upload: validate and finalize SINGLE or MULTIPART.

        SINGLE:  check_object_exists → status → DONE.
        MULTIPART: list_parts + complete_multipart_upload → DONE.

        Session goes directly to DONE (no UPLOAD_COMPLETED intermediate).
        DONE tickets are idempotent on re-call.
        """
        logger.info(
            "dispatch_complete_upload: transfer_id=%s, tenant=%s",
            transfer_id,
            tenant,
        )

        ticket = self._ticket_repo.get_by_transfer_id(transfer_id, tenant=tenant)
        if ticket is None:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        # Idempotency / terminal-state guard
        if ticket.status in ("DONE", "CANCELLED", "FAILED", "DELETED"):
            if ticket.status == "DONE":
                return SessionCompleteUploadResponse(
                    transfer_id=transfer_id,
                    status=ticket.status,
                )
            raise ValueError(
                f"Cannot complete transfer {transfer_id}: "
                f"ticket is in terminal state {ticket.status}"
            )

        if ticket.multipart_session_id:
            # MULTIPART: list_parts → complete → DONE
            parts = await asyncio.to_thread(
                self._file_transfer_backend.list_parts,
                ticket.fileservice_staging_path,
                ticket.multipart_session_id,
            )
            if not parts:
                raise ValueError(
                    f"No parts uploaded for transfer {transfer_id} — "
                    "cannot complete an empty multipart upload"
                )
            await asyncio.to_thread(
                self._file_transfer_backend.complete_multipart_upload,
                ticket.fileservice_staging_path,
                ticket.multipart_session_id,
                parts,
            )
        else:
            # SINGLE: check_object_exists
            exists = await asyncio.to_thread(
                self._file_transfer_backend.check_object_exists,
                ticket.fileservice_staging_path,
            )
            if not exists:
                raise ValueError(
                    f"OSS object not found at {ticket.fileservice_staging_path}"
                )

        # Session goes directly to DONE — no UPLOAD_COMPLETED intermediate
        try:
            self._ticket_repo.update_status(transfer_id, "DONE")
        except TransferStateConflictError:
            # CAS failed — a concurrent operation may have transitioned
            # this ticket.  Re-read and return success if already DONE.
            ticket = self._ticket_repo.get_by_transfer_id(
                transfer_id, tenant=tenant
            )
            if ticket is not None and ticket.status == "DONE":
                logger.info(
                    "dispatch_complete_upload: CAS conflict resolved — "
                    "ticket already DONE (transfer_id=%s)",
                    transfer_id,
                )
                return SessionCompleteUploadResponse(
                    transfer_id=transfer_id,
                    status=ticket.status,
                )
            raise

        return SessionCompleteUploadResponse(
            transfer_id=transfer_id,
            status="DONE",
        )

    # ------------------------------------------------------------------
    # dispatch_cancel_upload
    # ------------------------------------------------------------------

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
        logger.info(
            "dispatch_cancel_upload: transfer_id=%s, tenant=%s",
            transfer_id,
            tenant,
        )

        ticket = self._ticket_repo.get_by_transfer_id(transfer_id, tenant=tenant)
        if ticket is None:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        # Idempotency / terminal-state guard
        if ticket.status in ("CANCELLED", "FAILED", "DELETED", "DONE"):
            return SessionCancelUploadResponse(
                transfer_id=transfer_id,
                status=ticket.status,
            )

        if ticket.multipart_session_id:
            # Abort the OSS multipart session.  Tolerate NoSuchUpload —
            # the session may have been completed/aborted concurrently.
            try:
                await asyncio.to_thread(
                    self._file_transfer_backend.abort_multipart_upload,
                    ticket.fileservice_staging_path,
                    ticket.multipart_session_id,
                )
            except Exception as _abort_err:
                _msg = str(_abort_err)
                if "NoSuchUpload" in _msg or "not found" in _msg.lower():
                    logger.info(
                        "dispatch_cancel_upload: multipart session already "
                        "gone for transfer_id=%s (concurrent complete?), "
                        "proceeding to cancel ticket",
                        transfer_id,
                    )
                else:
                    raise

        # CAS-aware status update
        try:
            self._ticket_repo.update_status(transfer_id, "CANCELLED")
        except TransferStateConflictError:
            ticket = self._ticket_repo.get_by_transfer_id(
                transfer_id, tenant=tenant
            )
            if ticket is not None and ticket.status in (
                "CANCELLED",
                "FAILED",
                "DELETED",
                "DONE",
            ):
                logger.info(
                    "dispatch_cancel_upload: CAS conflict resolved — "
                    "ticket already in status=%s (transfer_id=%s)",
                    ticket.status,
                    transfer_id,
                )
                return SessionCancelUploadResponse(
                    transfer_id=transfer_id,
                    status=ticket.status,
                )
            raise

        return SessionCancelUploadResponse(
            transfer_id=transfer_id, status="CANCELLED"
        )

    # ------------------------------------------------------------------
    # dispatch_get_share_link
    # ------------------------------------------------------------------

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
        logger.info(
            "dispatch_get_share_link: transfer_id=%s, tenant=%s, "
            "session_id=%s, show=%s",
            transfer_id,
            tenant,
            session_id,
            show,
        )

        # Look up ticket with tenant filter (ownership validation per D-05)
        ticket = self._ticket_repo.get_by_transfer_id(transfer_id, tenant=tenant)
        if ticket is None:
            raise SourceTransferNotFoundError(transfer_id=transfer_id)

        # Verify session_id ownership (per D-05 — don't reveal existence)
        if ticket.session_id != session_id:
            raise SourceTransferNotFoundError(transfer_id=transfer_id)

        # Only DONE tickets are shareable
        if ticket.status != "DONE":
            raise SourceTransferNotReadyError(
                transfer_id=transfer_id,
                current_status=ticket.status,
            )

        # Build response_params based on show flag
        # show=False → attachment (download); show=True → inline (preview)
        response_params = None
        if not show:
            response_params = {
                "response-content-disposition": "attachment"
            }

        share_url = await asyncio.to_thread(
            self._file_transfer_backend.generate_download_url,
            ticket.fileservice_staging_path,
            expire_seconds,
            response_params,
        )

        expires_at = (
            datetime.now(UTC) + timedelta(seconds=expire_seconds)
        ).isoformat()

        return SessionShareLinkResponse(
            share_url=share_url,
            transfer_id=transfer_id,
            expires_at=expires_at,
        )

    # ------------------------------------------------------------------
    # dispatch_get_transfer_status
    # ------------------------------------------------------------------

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
        logger.info(
            "dispatch_get_transfer_status: transfer_id=%s, tenant=%s, "
            "session_id=%s",
            transfer_id,
            tenant,
            session_id,
        )

        record = self._ticket_repo.get_by_transfer_id(
            transfer_id,
            tenant=tenant,
        )
        if record is None:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        # Ownership validation — don't reveal existence (per D-05)
        if session_id is not None and record.session_id != session_id:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        # Conditional fields per status
        error_message = (
            record.error_message if record.status == "FAILED" else None
        )

        return SessionGetTransferStatusResponse(
            transfer_id=record.transfer_id,
            status=record.status,
            filename=record.filename,
            session_id=record.session_id,
            error_message=error_message,
            created_at=record.gmt_create.isoformat(),
            updated_at=record.gmt_modified.isoformat(),
            operator=record.operator,
        )

    # ------------------------------------------------------------------
    # dispatch_delete_transfer
    # ------------------------------------------------------------------

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
        logger.info(
            "dispatch_delete_transfer: transfer_id=%s, tenant=%s",
            transfer_id,
            tenant,
        )

        ticket = self._ticket_repo.get_by_transfer_id(
            transfer_id,
            tenant=tenant,
        )

        if ticket is None:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        # Terminal state validation (per D-04)
        terminal_states = {"DONE", "FAILED", "CANCELLED", "DELETED"}
        if ticket.status not in terminal_states:
            raise TransferNotTerminalError(
                transfer_id=ticket.transfer_id,
                status=ticket.status,
            )

        if ticket.status == "DELETED":
            # Already deleted — idempotent
            return SessionDeleteTransferResponse(
                transfer_id=ticket.transfer_id,
                previous_status="DELETED",
                new_status="DELETED",
            )

        previous_status = ticket.status

        # Delete OSS object — no head_object pre-check per D-06.
        # The backend's delete_object should handle NoSuchKey gracefully.
        await asyncio.to_thread(
            self._file_transfer_backend.delete_object,
            ticket.fileservice_staging_path,
        )

        # CAS-aware status update
        try:
            self._ticket_repo.update_status(ticket.transfer_id, "DELETED")
        except TransferStateConflictError:
            ticket = self._ticket_repo.get_by_transfer_id(
                ticket.transfer_id, tenant=tenant
            )
            if ticket is not None and ticket.status == "DELETED":
                logger.info(
                    "dispatch_delete_transfer: CAS conflict resolved — "
                    "ticket already DELETED (transfer_id=%s)",
                    ticket.transfer_id,
                )
                return SessionDeleteTransferResponse(
                    transfer_id=ticket.transfer_id,
                    previous_status=previous_status,
                    new_status="DELETED",
                )
            raise

        logger.info(
            "dispatch_delete_transfer: result: transfer_id=%s, "
            "previous_status=%s, new_status=DELETED",
            ticket.transfer_id,
            previous_status,
        )

        return SessionDeleteTransferResponse(
            transfer_id=ticket.transfer_id,
            previous_status=previous_status,
            new_status="DELETED",
        )