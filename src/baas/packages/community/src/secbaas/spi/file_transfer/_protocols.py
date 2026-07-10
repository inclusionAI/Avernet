from __future__ import annotations

from typing import Protocol


class FileTransferBackend(Protocol):
    """Protocol for file transfer storage backend operations.

    Implementations:
    - AliyunOssFileTransferBackend: production OSS operations via oss2 SDK.
    """

    def generate_upload_url(self, staging_path: str, expire_seconds: int) -> str:
        """Generate a presigned PUT URL for uploading a file to the staging path.

        The staging_path is a complete OSS object key constructed by the
        Dispatcher.  This method does NOT construct or transform the path.

        Args:
            staging_path: Complete OSS object key (constructed by Dispatcher).
            expire_seconds: URL validity duration in seconds.

        Returns:
            Presigned PUT URL string.
        """
        ...

    def check_object_exists(self, staging_path: str) -> bool:
        """Check whether an object exists at the given staging path.

        Uses OSS head_object to verify existence.  Leverages OSS atomic
        write semantics to confirm file upload completeness.

        Args:
            staging_path: Complete OSS object key.

        Returns:
            True if object exists and is readable, False otherwise.
        """
        ...

    def generate_download_url(self, staging_path: str, expire_seconds: int) -> str:
        """Generate a presigned GET URL for downloading a file from the staging path.

        The staging_path is a complete OSS object key constructed by the
        Dispatcher.  This method does NOT construct or transform the path.

        Args:
            staging_path: Complete OSS object key.
            expire_seconds: URL validity duration in seconds.

        Returns:
            Presigned GET URL string.
        """
        ...