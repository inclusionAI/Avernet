"""No-op FileTransferBackend for stub/singlebox/test modes.

Provides a safe zero-value that allows the DI container to resolve
without real OSS credentials.  All operations raise NotImplementedError
with a clear message — the caller is responsible for guarding with
feature-flag checks before invoking transfer operations.
"""

from secbaas.spi.file_transfer import (
    FileTransferBackend,
    MultipartSession,
    ObjectItem,
    ObjectListing,
    PartInfo,
)


class NoopFileTransferBackend(FileTransferBackend):
    """No-op implementation for when file transfer is disabled.

    Used in stub/singlebox mode where OSS credentials are not available.
    The DI container resolves this safely; any actual transfer operation
    raises NotImplementedError.
    """

    def generate_upload_url(self, staging_path: str, expire_seconds: int) -> str:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )

    def check_object_exists(self, staging_path: str) -> bool:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )

    def generate_download_url(self, staging_path: str, expire_seconds: int) -> str:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )

    def initiate_multipart_upload(
        self, staging_path: str, expire_seconds: int, part_count: int = 2
    ) -> MultipartSession:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )

    def list_parts(self, staging_path: str, session_id: str) -> list[PartInfo]:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )

    def complete_multipart_upload(
        self, staging_path: str, session_id: str, parts: list[PartInfo]
    ) -> None:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )

    def abort_multipart_upload(self, staging_path: str, session_id: str) -> None:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )

    def list_objects(
        self, prefix: str, limit: int, marker: str | None
    ) -> ObjectListing:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )

    def delete_object(self, key: str) -> None:
        raise NotImplementedError(
            "File transfer is not configured. "
            "Set config.plugins.file_transfer to 'oss' to enable."
        )