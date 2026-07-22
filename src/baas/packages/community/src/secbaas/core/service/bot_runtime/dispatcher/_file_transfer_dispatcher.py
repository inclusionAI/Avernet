"""DefaultBotFileTransferDispatcher — file upload/download orchestration.

Extends BaseDispatcher to implement the BotFileTransferDispatcher protocol.
Encapsulates upload (D-04) and download (D-10) business flows including
staging path construction, pre-signed URL generation, ticket creation,
and device-side push_file for downloads.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from secbaas.api.bot_runtime import (
    BotFileTransferDispatcher,
    BotNotFoundError,
    CancelUploadResponse,
    CompleteUploadResponse,
    GetDownloadUrlResponse,
    GetTransferStatusResponse,
    GetUploadUrlResponse,
    ShareLinkResponse,
    StagingDeleteResponse,
    StagingListResponse,
    TransferNotFoundError,
    TransferStateConflictError,
)
from secbaas.core.service.paas import PaasServiceFacade
from secbaas.core.utils.env_utils import get_current_env
from secbaas.logger import get_logger

if TYPE_CHECKING:
    from secbaas.core.repository.bot import BotRepository
    from secbaas.core.repository.device import DeviceRepository
    from secbaas.core.repository.file_transfer_ticket import TicketRepository
    from secbaas.spi.file_transfer import FileTransferBackend

from ._base_dispatcher import BotBaseDispatcher

logger = get_logger("core-service")

# v1.5 multipart routing thresholds
MULTIPART_THRESHOLD = 104_857_600  # 100MB
DEFAULT_PART_SIZE = 10_485_760  # 10MB


class DefaultBotFileTransferDispatcher(BotBaseDispatcher, BotFileTransferDispatcher):
    """File transfer dispatcher for bot devices.

    Implements BotFileTransferDispatcher protocol.
    Inherits __init__ and _resolve_bot_device from BotBaseDispatcher.

    Conventions:
    - paas_device_id="" is the sentinel for retention-mode tickets
      (device_path=None at upload time).  Downstream code must use
      ``paas_device_id == ""`` rather than truthiness checks.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        paas_facade: PaasServiceFacade,
        file_transfer_backend: FileTransferBackend,
        ticket_repo: TicketRepository,
    ):
        super().__init__(bot_repo, device_repo, paas_facade)
        self._file_transfer_backend = file_transfer_backend
        self._ticket_repo = ticket_repo

    async def dispatch_get_upload_url(
        self,
        bot_uuid: str,
        tenant: str,
        device_path: str | None = None,
        filename: str | None = None,
        expire_seconds: int = 3600,
        staging_subdir: str | None = None,
        device_affinity: str | None = None,
        file_size: int = 0,
        part_size: int | None = None,
        operator: str | None = None,
    ) -> GetUploadUrlResponse:
        """Orchestrate upload URL generation (v1.5: D-01/D-02/D-05 flow).

        Steps:
        1. Resolve bot to active device (SKIP in retention mode, device_path=None)
        2. Validate staging_subdir (D-01: reject "..", strip "/")
        3. Construct OSS staging path
        4. Route to SINGLE or MULTIPART based on file_size vs threshold:
           - MULTIPART (file_size >= 100MB): initiate multipart session, return parts list
           - SINGLE: generate pre-signed PUT URL
        5. Create ticket record
        6. Return response with upload_url (SINGLE) or parts list (MULTIPART)
        """
        env = get_current_env()
        logger.info(
            "Dispatching upload URL: bot_uuid=%s, device_path=%s, tenant=%s, "
            "file_size=%d",
            bot_uuid,
            device_path,
            tenant,
            file_size,
        )
        # D-04: Normalize empty/None operator to "unknown" before any DB write
        if not operator or not operator.strip():
            operator = "unknown"

        # D-05: Retention mode — device_path is None, skip device resolution
        if device_path is not None:
            _, _, paas_device_id = await self._resolve_bot_device(
                bot_uuid=bot_uuid,
                tenant=tenant,
                env=env,
                device_affinity=device_affinity,
            )
            logger.info("Resolved device for upload: paas_device_id=%s", paas_device_id)
        else:
            # Retention mode: file stays in OSS only, no device involvement
            paas_device_id = ""  # sentinel for "no device" (D-05)
            logger.info(
                "Retention mode upload: bot_uuid=%s (no device resolution)",
                bot_uuid,
            )

        # D-01: Validate staging_subdir
        if staging_subdir is not None:
            if ".." in staging_subdir:
                raise ValueError("staging_subdir contains invalid path traversal")
            staging_subdir = staging_subdir.strip("/")

        transfer_id = uuid.uuid4().hex
        if device_path is not None and ".." in device_path:
            raise ValueError("device_path contains invalid path traversal")
        resolved_filename = filename or (
            Path(device_path).name if device_path else "untitled"
        )

        # Construct staging path via backend (D-14)
        staging_path = self._file_transfer_backend.build_staging_path(
            tenant=tenant,
            transfer_id=transfer_id,
            filename=resolved_filename,
            subdir=staging_subdir,
        )

        expires_at = (
            datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expire_seconds)
        ).isoformat()

        # Validate file_size before routing (applies to both SINGLE and MULTIPART)
        if file_size < 0:
            raise ValueError(f"file_size must be non-negative, got {file_size}")

        # D-01/D-02: SINGLE/MULTIPART routing
        if file_size >= MULTIPART_THRESHOLD:
            # MULTIPART path
            effective_part_size = part_size if part_size else DEFAULT_PART_SIZE
            if effective_part_size <= 0:
                raise ValueError(f"part_size must be positive, got {part_size}")
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
                    "expires_at": expires_at,
                }
                for p in multipart_session.parts
            ]

            # Create ticket with multipart_session_id
            await asyncio.to_thread(
                self._ticket_repo.create_ticket,
                transfer_id=transfer_id,
                tenant=tenant,
                paas_device_id=paas_device_id,
                direction="UPLOAD",
                status="CREATED",
                staging_subdir=staging_subdir,
                filename=resolved_filename,
                device_path=device_path,
                fileservice_staging_path=staging_path,
                error_message=None,
                multipart_session_id=multipart_session.session_id,
                operator=operator,
            )

            logger.info(
                "Ticket created (MULTIPART): transfer_id=%s, direction=UPLOAD",
                transfer_id,
            )

            return GetUploadUrlResponse(
                type="MULTIPART",
                upload_session_id=multipart_session.session_id,
                part_size=effective_part_size,
                part_count=part_count,
                parts=parts_data,
                transfer_id=transfer_id,
                expires_at=expires_at,
            )
        else:
            # SINGLE path (existing logic + retention mode)
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

            await asyncio.to_thread(
                self._ticket_repo.create_ticket,
                transfer_id=transfer_id,
                tenant=tenant,
                paas_device_id=paas_device_id,
                direction="UPLOAD",
                status="CREATED",
                staging_subdir=staging_subdir,
                filename=resolved_filename,
                device_path=device_path,
                fileservice_staging_path=staging_path,
                error_message=None,
                operator=operator,
            )

            logger.info(
                "Ticket created (SINGLE): transfer_id=%s, direction=UPLOAD", transfer_id
            )

            return GetUploadUrlResponse(
                upload_url=upload_url,
                transfer_id=transfer_id,
                expires_at=expires_at,
                type="SINGLE",
            )

    async def dispatch_get_download_url(
        self,
        bot_uuid: str,
        tenant: str,
        device_path: str,
        expire_seconds: int = 3600,
        device_affinity: str | None = None,
        operator: str | None = None,
    ) -> GetDownloadUrlResponse:
        """Orchestrate download URL request (D-10 flow).

        Steps:
        1. Resolve bot to active device
        2. Extract filename from device_path (D-06)
        3. Generate pre-signed PUT URL for device to upload to OSS
        4. Immediately trigger push_file on device (D-09)
        5. Create ticket record
        6. Return transfer_id (no download_url per D-08)
        """
        env = get_current_env()
        logger.info(
            "Dispatching download URL: bot_uuid=%s, device_path=%s, tenant=%s",
            bot_uuid,
            device_path,
            tenant,
        )
        # D-04: Normalize empty/None operator to "unknown" before any DB write
        if not operator or not operator.strip():
            operator = "unknown"

        _, _, paas_device_id = await self._resolve_bot_device(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            device_affinity=device_affinity,
        )

        logger.info("Resolved device for download: paas_device_id=%s", paas_device_id)

        # WR-03: Defense-in-depth path traversal check — same guard as upload path
        if ".." in device_path:
            raise ValueError("device_path contains invalid path traversal")

        # D-06: Extract filename from device_path
        filename = Path(device_path).name
        transfer_id = uuid.uuid4().hex

        # Construct staging path via backend (no staging_subdir for download)
        staging_path = self._file_transfer_backend.build_staging_path(
            tenant=tenant,
            transfer_id=transfer_id,
            filename=filename,
        )

        # Generate PUT URL for device to upload file to OSS
        target_url = await asyncio.to_thread(
            self._file_transfer_backend.generate_upload_url,
            staging_path,
            expire_seconds,
        )

        logger.info(
            "Download PUT URL generated: transfer_id=%s, staging_path=%s",
            transfer_id,
            staging_path,
        )

        # Create ticket FIRST: if push_file fails after ticket creation,
        # the poller will time out the CREATED ticket → FAILED, providing
        # clean lifecycle management. The opposite order (push_file first,
        # then create_ticket) risks orphaned OSS objects if create_ticket fails.
        await asyncio.to_thread(
            self._ticket_repo.create_ticket,
            transfer_id=transfer_id,
            tenant=tenant,
            paas_device_id=paas_device_id,
            direction="DOWNLOAD",
            status="CREATED",
            staging_subdir=None,
            filename=filename,
            device_path=device_path,
            fileservice_staging_path=staging_path,
            error_message=None,
            operator=operator,
        )

        logger.info("Ticket created: transfer_id=%s, direction=DOWNLOAD", transfer_id)

        # D-09: Trigger device upload via paas_facade
        await self._paas_facade.push_file(
            paas_device_id=paas_device_id,
            device_path=device_path,
            target_url=target_url,
        )

        logger.info(
            "Push file triggered: paas_device_id=%s, device_path=%s",
            paas_device_id,
            device_path,
        )

        expires_at = (
            datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expire_seconds)
        ).isoformat()
        return GetDownloadUrlResponse(
            transfer_id=transfer_id,
            expires_at=expires_at,
        )

    async def dispatch_get_transfer_status(
        self,
        transfer_id: str,
        tenant: str | None = None,
        bot_uuid: str | None = None,
    ) -> GetTransferStatusResponse:
        """Query a transfer ticket by transfer_id (D-12 query flow).

        Maps TicketRecord fields to GetTransferStatusResponse with
        conditional URL/error fields based on ticket status.

        When bot_uuid is provided, validates that the bot exists and belongs
        to the specified tenant before returning the transfer status.
        """
        # Validate bot ownership when bot_uuid is provided
        if bot_uuid is not None and tenant is not None:
            env = get_current_env()
            bots = self._bot_repo.list_by_bot_uuid(bot_uuid, tenant, env)
            if not bots:
                raise BotNotFoundError(bot_uuid)

        record = self._ticket_repo.get_by_transfer_id(
            transfer_id,
            tenant=tenant,
        )
        if record is None:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        # Conditional fields per status
        download_url = record.download_url if record.status == "DONE" else None
        upload_url = record.upload_url if record.status == "CREATED" else None
        # OSS presigned URLs embed their own expiry — expires_at is null for transfer queries
        expires_at: str | None = None

        error_message = record.error_message if record.status == "FAILED" else None

        return GetTransferStatusResponse(
            transfer_id=record.transfer_id,
            status=record.status,
            direction=record.direction,
            filename=record.filename,
            device_path=record.device_path,
            download_url=download_url,
            upload_url=upload_url,
            expires_at=expires_at,
            error_message=error_message,
            created_at=record.gmt_create.isoformat(),
            updated_at=record.gmt_modified.isoformat(),
            operator=record.operator,
        )

    # ------------------------------------------------------------------
    # v1.5 new dispatch methods
    # ------------------------------------------------------------------

    async def dispatch_complete_upload(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> CompleteUploadResponse:
        """Complete an upload (D-03): validate and finalize SINGLE or MULTIPART.

        SINGLE:  check_object_exists + status -> UPLOAD_COMPLETED.
        MULTIPART: list_parts + complete_multipart_upload -> UPLOAD_COMPLETED.

        Idempotency guard: if the ticket has already transitioned past
        UPLOAD_COMPLETED, return the current status instead of calling
        OSS operations (which would fail on an already-completed multipart).
        """
        logger.info(
            "dispatch_complete_upload: transfer_id=%s, tenant=%s",
            transfer_id,
            tenant,
        )

        ticket = self._ticket_repo.get_by_transfer_id(transfer_id, tenant=tenant)
        if ticket is None:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        # Idempotency / terminal-state guard: reject complete on
        # CANCELLED / FAILED / DELETED tickets whose multipart
        # sessions have already been torn down.
        if ticket.status in (
            "UPLOAD_COMPLETED",
            "PULLING",
            "DONE",
            "CANCELLED",
            "FAILED",
            "DELETED",
        ):
            if ticket.status not in ("UPLOAD_COMPLETED", "PULLING", "DONE"):
                raise ValueError(
                    f"Cannot complete transfer {transfer_id}: "
                    f"ticket is in terminal state {ticket.status}"
                )
            return CompleteUploadResponse(
                transfer_id=transfer_id,
                status=ticket.status,
            )

        if ticket.multipart_session_id:
            # MULTIPART: list_parts -> complete -> UPLOAD_COMPLETED
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
                from secbaas.api.bot_runtime import (
                    OssObjectNotFoundError,
                )

                raise OssObjectNotFoundError(
                    staging_path=ticket.fileservice_staging_path,
                )

        try:
            self._ticket_repo.update_status(transfer_id, "UPLOAD_COMPLETED")
        except TransferStateConflictError:
            # CAS failed — the poller may have already processed this ticket
            # between our read and the CAS.  Re-read and return success if the
            # ticket has already reached a valid post-completion state.
            ticket = self._ticket_repo.get_by_transfer_id(transfer_id, tenant=tenant)
            if ticket is not None and ticket.status in (
                "UPLOAD_COMPLETED",
                "PULLING",
                "DONE",
            ):
                logger.info(
                    "dispatch_complete_upload: CAS conflict resolved — "
                    "ticket already in status=%s (transfer_id=%s)",
                    ticket.status,
                    transfer_id,
                )
                return CompleteUploadResponse(
                    transfer_id=transfer_id,
                    status=ticket.status,
                )
            raise

        return CompleteUploadResponse(
            transfer_id=transfer_id,
            status="UPLOAD_COMPLETED",
        )

    async def dispatch_cancel_upload(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> CancelUploadResponse:
        """Cancel an upload (D-04): abort multipart session, transition to CANCELLED.

        If the ticket has an active multipart session, abort it on OSS.
        Then transition the ticket to the CANCELLED terminal state.
        """
        logger.info(
            "dispatch_cancel_upload: transfer_id=%s, tenant=%s",
            transfer_id,
            tenant,
        )

        ticket = self._ticket_repo.get_by_transfer_id(transfer_id, tenant=tenant)
        if ticket is None:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        # Idempotency / terminal-state guard: if the ticket is in
        # a terminal state the multipart session no longer exists
        # (or was never created for SINGLE).  Calling abort_multipart_upload
        # on a completed/aborted session would raise NoSuchUpload from OSS.
        #
        # DONE / PULLING: download completion states — multipart session
        #   already gone; return idempotent success.
        # UPLOAD_COMPLETED: upload already finalized on OSS — cannot
        #   cancel without orphaning the object (SINGLE) or hitting
        #   NoSuchUpload (MULTIPART).  Raise to reject the request.
        if ticket.status in ("CANCELLED", "FAILED", "DELETED", "DONE", "PULLING"):
            return CancelUploadResponse(
                transfer_id=transfer_id,
                status=ticket.status,
            )

        if ticket.status == "UPLOAD_COMPLETED":
            raise ValueError(
                f"Cannot cancel transfer {transfer_id}: "
                f"upload is already completed (status={ticket.status})"
            )

        if ticket.multipart_session_id:
            # WR-02: If the multipart session was already completed or
            # aborted by a concurrent complete_upload, abort_multipart_upload
            # raises NoSuchUpload from OSS.  Treat as idempotent — the
            # session no longer needs aborting; proceed to cancel the ticket.
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

        # WR-01: CAS-aware status update with TransferStateConflictError
        # recovery — same pattern as dispatch_complete_upload (CR-02).
        try:
            self._ticket_repo.update_status(transfer_id, "CANCELLED")
        except TransferStateConflictError:
            ticket = self._ticket_repo.get_by_transfer_id(transfer_id, tenant=tenant)
            if ticket is not None:
                if ticket.status in (
                    "CANCELLED",
                    "FAILED",
                    "DELETED",
                    "DONE",
                    "PULLING",
                ):
                    logger.info(
                        "dispatch_cancel_upload: CAS conflict resolved — "
                        "ticket already in status=%s (transfer_id=%s)",
                        ticket.status,
                        transfer_id,
                    )
                    return CancelUploadResponse(
                        transfer_id=transfer_id,
                        status=ticket.status,
                    )
                if ticket.status == "UPLOAD_COMPLETED":
                    raise ValueError(
                        f"Cannot cancel transfer {transfer_id}: "
                        f"upload is already completed "
                        f"(status={ticket.status})"
                    )
            raise

        return CancelUploadResponse(transfer_id=transfer_id, status="CANCELLED")

    async def dispatch_list_staging(
        self,
        prefix: str,
        limit: int = 100,
        marker: str | None = None,
        tenant: str | None = None,
    ) -> StagingListResponse:
        """List OSS staging objects with marker pagination (D-07/D-08).

        Pure OSS operation — no device involvement, no PaaS layer.
        Returns flat list of objects matching the prefix.
        """

        # Tenant-scoped prefix: every listing is automatically scoped to
        # the authenticated tenant's staging root, preventing
        # cross-tenant metadata leakage.
        if tenant is not None:
            prefix_subdir = None
            if prefix:
                # Normalize user-provided prefix: strip legacy hardcoded
                # prefixes if present, then strip leading/trailing slashes
                # to avoid double-slash paths.
                user_sub = prefix
                if user_sub.startswith("file-transfers/"):
                    user_sub = user_sub[len("file-transfers/") :]
                elif user_sub.startswith("baas-file-transfer/"):
                    user_sub = user_sub[len("baas-file-transfer/") :]
                elif user_sub in ("file-transfers", "baas-file-transfer"):
                    user_sub = ""
                user_sub = user_sub.strip("/")
                if ".." in user_sub:
                    raise ValueError("prefix contains invalid path traversal")
                if user_sub:
                    prefix_subdir = user_sub
            effective_prefix = self._file_transfer_backend.build_staging_prefix(
                tenant=tenant,
                subdir=prefix_subdir,
            )
        else:
            # Defense-in-depth: validate prefix even when tenant scoping
            # is skipped — prevents path traversal in raw prefix input
            if prefix and ".." in prefix:
                raise ValueError("prefix contains invalid path traversal")
            effective_prefix = prefix

        logger.info(
            "dispatch_list_staging: prefix=%s, effective_prefix=%s, "
            "limit=%d, marker=%s, tenant=%s",
            prefix,
            effective_prefix,
            limit,
            marker,
            tenant,
        )

        result = await asyncio.to_thread(
            self._file_transfer_backend.list_objects,
            effective_prefix,
            limit,
            marker,
        )

        return StagingListResponse(
            prefix=prefix,
            items=[
                {
                    "key": item.key,
                    "size": item.size,
                    "last_modified": item.last_modified,
                }
                for item in result.items
            ],
            truncated=result.truncated,
            next_marker=result.next_marker,
        )

    async def dispatch_delete_staging(
        self,
        key: str,
        tenant: str | None = None,
    ) -> StagingDeleteResponse:
        """Delete a staging object (D-09).

        Validates the associated ticket is in a terminal state
        (DONE/FAILED/CANCELLED/DELETED) before deleting.  Already-DELETED
        tickets are handled idempotently.
        """

        logger.info(
            "dispatch_delete_staging: key=%s, tenant=%s",
            key,
            tenant,
        )

        ticket = self._ticket_repo.get_by_fileservice_staging_path(
            key,
            tenant=tenant,
        )

        if ticket is None:
            raise TransferNotFoundError(
                f"No ticket found for staging key: {key}",
            )

        # D-09: validate terminal state
        terminal_states = {"DONE", "FAILED", "CANCELLED", "DELETED"}
        if ticket.status not in terminal_states:
            from secbaas.api.bot_runtime import (
                TransferNotTerminalError,
            )

            raise TransferNotTerminalError(
                transfer_id=ticket.transfer_id,
                status=ticket.status,
            )

        if ticket.status == "DELETED":
            # Already deleted — idempotent
            return StagingDeleteResponse(
                deleted_key=key,
                transfer_id=ticket.transfer_id,
                previous_status="DELETED",
                new_status="DELETED",
            )

        previous_status = ticket.status
        await asyncio.to_thread(
            self._file_transfer_backend.delete_object,
            key,
        )
        self._ticket_repo.update_status(ticket.transfer_id, "DELETED")
        return StagingDeleteResponse(
            deleted_key=key,
            transfer_id=ticket.transfer_id,
            previous_status=previous_status,
            new_status="DELETED",
        )

    async def dispatch_generate_share_link(
        self,
        transfer_id: str,
        expire_seconds: int = 86400,
        tenant: str | None = None,
    ) -> ShareLinkResponse:
        """Generate a shareable download URL for a DONE transfer (D-12/D-13).

        Only DONE tickets are eligible.  The share URL is a pre-signed OSS
        GET URL with bounded expiry (default 24h, max 7d).

        generate_download_url produces the same pre-signed GET URL; the
        distinction between "download" and "share" is semantic (explicit
        POST vs implicit GET) rather than technical.
        """
        logger.info(
            "dispatch_generate_share_link: transfer_id=%s, "
            "expire_seconds=%d, tenant=%s",
            transfer_id,
            expire_seconds,
            tenant,
        )

        ticket = self._ticket_repo.get_by_transfer_id(transfer_id, tenant=tenant)
        if ticket is None:
            raise TransferNotFoundError(f"Transfer not found: {transfer_id}")

        if ticket.status != "DONE":
            raise ValueError(
                f"Share link requires ticket status DONE, got {ticket.status}",
            )

        share_url = await asyncio.to_thread(
            self._file_transfer_backend.generate_download_url,
            ticket.fileservice_staging_path,
            expire_seconds,
        )

        expires_at = (
            datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=expire_seconds)
        ).isoformat()
        return ShareLinkResponse(
            share_url=share_url,
            transfer_id=transfer_id,
            expires_at=expires_at,
        )
