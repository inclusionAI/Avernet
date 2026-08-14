"""Contracts for Session File share-link downloads and controlled re-uploads."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.session_files.models import (
    BaasFileExportShareLink,
    SessionFileTransferRequest,
    SessionFileUploadGrant,
)


class BaasFileExportError(RuntimeError):
    """A normalized BaaS export failure that is safe to expose as an error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@runtime_checkable
class BaasSessionFileClient(Protocol):
    async def create_share_link(
        self,
        request: SessionFileTransferRequest,
        *,
        expire_seconds: int,
    ) -> BaasFileExportShareLink: ...

    async def create_upload_grant(
        self,
        request: SessionFileTransferRequest,
        *,
        filename: str,
        size_bytes: int,
    ) -> SessionFileUploadGrant: ...

    async def upload_file(
        self,
        grant: SessionFileUploadGrant,
        source_path: str,
        *,
        resource_id: str,
    ) -> None: ...

    async def complete_upload(
        self,
        request: SessionFileTransferRequest,
    ) -> None: ...
