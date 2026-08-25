"""No-op FileTransferBackend for stub/singlebox/test modes.

Provides a safe zero-value that allows the DI container to resolve
without real OSS credentials.  All operations raise NotImplementedError
with a clear message — the caller is responsible for guarding with
feature-flag checks before invoking transfer operations.
"""

from secbaas.community.spi.file_transfer import (
    FileTransferBackend,
    MultipartSession,
    PartInfo,
)

_DISABLED_MESSAGE = "file_transfer is disabled in this deployment"


class NoopFileTransferBackend(FileTransferBackend):
    """No-op implementation for when file transfer is disabled.

    Used in stub/singlebox mode where OSS credentials are not available.
    The DI container resolves this safely; any actual transfer operation
    raises NotImplementedError.
    """

    @property
    def disabled(self) -> bool:
        """Report that this no-op backend is disabled."""
        return True

    def generate_upload_url(
        self, staging_path: str, expire_seconds: int, content_type: str | None = None
    ) -> str:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def check_object_exists(self, staging_path: str) -> bool:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def generate_download_url(
        self,
        staging_path: str,
        expire_seconds: int,
        response_params: dict | None = None,
    ) -> str:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def initiate_multipart_upload(
        self,
        staging_path: str,
        expire_seconds: int,
        part_count: int = 2,
        content_type: str | None = None,
    ) -> MultipartSession:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def list_parts(self, staging_path: str, session_id: str) -> list[PartInfo]:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def complete_multipart_upload(
        self, staging_path: str, session_id: str, parts: list[PartInfo]
    ) -> None:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def abort_multipart_upload(self, staging_path: str, session_id: str) -> None:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def delete_object(self, key: str) -> None:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def build_staging_path(
        self,
        tenant: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        raise NotImplementedError(_DISABLED_MESSAGE)

    def build_session_staging_path(
        self,
        tenant: str,
        session_id: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        raise NotImplementedError(_DISABLED_MESSAGE)
