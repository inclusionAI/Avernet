"""Bot File Transfer Dispatcher Protocol

Defines the BotFileTransferDispatcher protocol interface for the
packages tree (secbaas.* import convention).

This is a minimal mirror of the flat tree's _protocols.py, containing
only BotFileTransferDispatcher — the protocol needed by the file transfer
dispatcher and router in the packages tree.
"""

from typing import Protocol, runtime_checkable

from ._file_transfer_models import (
    CancelUploadResponse,
    CompleteUploadResponse,
    DeleteTransferResponse,
    GetDownloadUrlResponse,
    GetTransferStatusResponse,
    GetUploadUrlResponse,
    ShareLinkResponse,
)


@runtime_checkable
class BotFileTransferDispatcher(Protocol):
    """File transfer dispatcher protocol."""

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
    ) -> GetUploadUrlResponse: ...

    async def dispatch_get_download_url(
        self,
        bot_uuid: str,
        tenant: str,
        device_path: str,
        expire_seconds: int = 3600,
        device_affinity: str | None = None,
        operator: str | None = None,
    ) -> GetDownloadUrlResponse: ...

    async def dispatch_get_transfer_status(
        self,
        transfer_id: str,
        tenant: str | None = None,
        bot_uuid: str | None = None,
    ) -> GetTransferStatusResponse: ...

    async def dispatch_complete_upload(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> CompleteUploadResponse: ...

    async def dispatch_cancel_upload(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> CancelUploadResponse: ...

    async def dispatch_delete_transfer(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> DeleteTransferResponse:
        """Delete a transfer ticket and its associated OSS staging object.

        Only tickets in terminal states (DONE/FAILED/CANCELLED/DELETED) can be
        deleted. Already-DELETED tickets return 200 (idempotent).
        """
        ...

    async def dispatch_generate_share_link(
        self,
        transfer_id: str,
        expire_seconds: int = 86400,
        tenant: str | None = None,
    ) -> ShareLinkResponse: ...
