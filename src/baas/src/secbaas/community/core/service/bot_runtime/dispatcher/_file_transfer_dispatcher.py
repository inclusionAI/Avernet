"""DefaultBotFileTransferDispatcher — file upload/download orchestration.

Extends BaseDispatcher to implement the BotFileTransferDispatcher protocol.
Encapsulates upload (D-04) and download (D-10) business flows including
staging path construction, pre-signed URL generation, ticket creation,
and device-side push_file for downloads.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from secbaas.api.bot_runtime import BotFileTransferDispatcher
from secbaas.api.bot_runtime._file_transfer_models import (
    GetDownloadUrlResponse,
    GetTransferStatusResponse,
    GetUploadUrlResponse,
    TransferNotFoundError,
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


class DefaultBotFileTransferDispatcher(BotBaseDispatcher, BotFileTransferDispatcher):
    """File transfer dispatcher for bot devices.

    Implements BotFileTransferDispatcher protocol.
    Inherits __init__ and _resolve_bot_device from BotBaseDispatcher.
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
        device_path: str,
        filename: str | None = None,
        expire_seconds: int = 3600,
        staging_subdir: str | None = None,
        device_affinity: str | None = None,
    ) -> GetUploadUrlResponse:
        """Orchestrate upload URL generation (D-04 flow).

        Steps:
        1. Resolve bot to active device
        2. Validate staging_subdir (D-01: reject "..", strip "/")
        3. Construct OSS staging path
        4. Generate pre-signed PUT URL via FileTransferBackend
        5. Create ticket record
        6. Return response with upload_url + transfer_id
        """
        env = get_current_env()
        logger.info(
            "Dispatching upload URL: bot_uuid=%s, device_path=%s, tenant=%s",
            bot_uuid, device_path, tenant,
        )

        _, _, paas_device_id = await self._resolve_bot_device(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            device_affinity=device_affinity,
        )

        logger.info("Resolved device for upload: paas_device_id=%s", paas_device_id)

        # D-01: Validate staging_subdir
        if staging_subdir is not None:
            if ".." in staging_subdir:
                raise ValueError("staging_subdir contains invalid path traversal")
            staging_subdir = staging_subdir.strip("/")

        transfer_id = uuid.uuid4().hex
        resolved_filename = filename or Path(device_path).name

        # Construct staging path (Phase 67 formula)
        subdir_part = f"{staging_subdir}/" if staging_subdir else ""
        staging_path = f"file-transfers/{subdir_part}{transfer_id}/{resolved_filename}"

        # Generate pre-signed PUT URL (Pitfall #3: backend is sync)
        upload_url = await asyncio.to_thread(
            self._file_transfer_backend.generate_upload_url,
            staging_path,
            expire_seconds,
        )

        logger.info(
            "Upload URL generated: transfer_id=%s, staging_path=%s",
            transfer_id, staging_path,
        )

        # Create ticket (Pitfall #6: keyword is fileservice_staging_path)
        self._ticket_repo.create_ticket(
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
        )

        logger.info("Ticket created: transfer_id=%s, direction=UPLOAD", transfer_id)

        expires_at = (datetime.utcnow() + timedelta(seconds=expire_seconds)).isoformat()
        return GetUploadUrlResponse(
            upload_url=upload_url,
            transfer_id=transfer_id,
            expires_at=expires_at,
        )

    async def dispatch_get_download_url(
        self,
        bot_uuid: str,
        tenant: str,
        device_path: str,
        expire_seconds: int = 3600,
        device_affinity: str | None = None,
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
            bot_uuid, device_path, tenant,
        )

        _, _, paas_device_id = await self._resolve_bot_device(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            device_affinity=device_affinity,
        )

        logger.info("Resolved device for download: paas_device_id=%s", paas_device_id)

        # D-06: Extract filename from device_path
        filename = Path(device_path).name
        transfer_id = uuid.uuid4().hex

        # Construct staging path (no staging_subdir for download)
        staging_path = f"file-transfers/{transfer_id}/{filename}"

        # Generate PUT URL for device to upload file to OSS
        target_url = await asyncio.to_thread(
            self._file_transfer_backend.generate_upload_url,
            staging_path,
            expire_seconds,
        )

        logger.info(
            "Download PUT URL generated: transfer_id=%s, staging_path=%s",
            transfer_id, staging_path,
        )

        # D-09: Immediately trigger device upload via paas_facade
        await self._paas_facade.push_file(
            paas_device_id=paas_device_id,
            device_path=device_path,
            target_url=target_url,
        )

        logger.info(
            "Push file triggered: paas_device_id=%s, device_path=%s",
            paas_device_id, device_path,
        )

        # Create ticket
        self._ticket_repo.create_ticket(
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
        )

        logger.info("Ticket created: transfer_id=%s, direction=DOWNLOAD", transfer_id)

        expires_at = (datetime.utcnow() + timedelta(seconds=expire_seconds)).isoformat()
        return GetDownloadUrlResponse(
            transfer_id=transfer_id,
            expires_at=expires_at,
        )

    async def dispatch_get_transfer_status(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> GetTransferStatusResponse:
        """Query a transfer ticket by transfer_id (D-12 query flow).

        Maps TicketRecord fields to GetTransferStatusResponse with
        conditional URL/error fields based on ticket status.
        """
        record = self._ticket_repo.get_by_transfer_id(
            transfer_id, tenant=tenant,
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
        )