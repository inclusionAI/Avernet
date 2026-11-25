from typing import Protocol, Optional, BinaryIO


class ObjectStorageProvider(Protocol):
    """Public object storage provider contract.

    Implementations may be OSS defaults (filesystem, S3-compatible) or internal plugins.
    Public code must depend on this contract, not internal storage SDKs.
    """

    def upload(self, key: str, data: bytes, content_type: str = None) -> bool:
        """Upload object.

        Args:
            key: Object key
            data: Object data
            content_type: Optional content type

        Returns:
            True if upload successful, False otherwise.
        """
        ...

    def download(self, key: str) -> Optional[bytes]:
        """Download object.

        Args:
            key: Object key

        Returns:
            Object data if found, None otherwise.
        """
        ...

    def delete(self, key: str) -> bool:
        """Delete object.

        Args:
            key: Object key

        Returns:
            True if deletion successful, False otherwise.
        """
        ...

    def exists(self, key: str) -> bool:
        """Check if object exists.

        Args:
            key: Object key

        Returns:
            True if object exists, False otherwise.
        """
        ...

    def get_url(self, key: str, expires_in: int = 3600) -> Optional[str]:
        """Get presigned URL for object.

        Args:
            key: Object key
            expires_in: URL expiration in seconds

        Returns:
            Presigned URL if available, None otherwise.
        """
        ...