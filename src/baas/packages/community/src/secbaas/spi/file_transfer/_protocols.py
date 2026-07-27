from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class MultipartSession:
    """Result of initiating a multipart upload.

    Fields:
        session_id: Upload session identifier for the multipart session.
        part_count: ceil(file_size / part_size).
        parts: Pre-generated list of {part_number, upload_url} for each part.
    """

    session_id: str
    part_count: int
    parts: list[PartInfo]


@dataclass(slots=True)
class PartInfo:
    """Information about a single multipart upload part.

    Fields:
        part_number: 1-based part number within the upload.
        upload_url: Pre-signed per-part PUT URL.
        etag: ETag returned by the storage backend after successful part upload. None in init response.
    """

    part_number: int
    upload_url: str
    etag: str | None = None


@dataclass(slots=True)
class ObjectItem:
    """Metadata for a single staging object returned by list_objects.

    Fields:
        key: Full object storage key.
        size: Object size in bytes.
        last_modified: ISO-format timestamp of last modification.
    """

    key: str
    size: int
    last_modified: str


@dataclass(slots=True)
class ObjectListing:
    """Paginated result of list_objects.

    Fields:
        items: List of ObjectItem for the current page.
        truncated: True if more results exist beyond this page.
        next_marker: Opaque pagination token for the next page (None if not truncated).
    """

    items: list[ObjectItem]
    truncated: bool
    next_marker: str | None


class FileTransferBackend(Protocol):
    """Protocol for file transfer storage backend operations.

    Implementations:
    - AliyunOssFileTransferBackend: production OSS operations via oss2 SDK.
    """

    def generate_upload_url(self, staging_path: str, expire_seconds: int) -> str:
        """Generate a presigned PUT URL for uploading a file to the staging path.

        The staging_path is a complete object storage key constructed by the
        Dispatcher.  This method does NOT construct or transform the path.

        Args:
            staging_path: Complete object storage key (constructed by Dispatcher).
            expire_seconds: URL validity duration in seconds.

        Returns:
            Presigned PUT URL string.
        """
        ...

    def check_object_exists(self, staging_path: str) -> bool:
        """Check whether an object exists at the given staging path.

        Uses an HTTP head request to verify existence.  Leverages atomic
        write semantics of the underlying storage to confirm file upload completeness.

        Args:
            staging_path: Complete object storage key.

        Returns:
            True if object exists and is readable.
            False if object does not exist (NoSuchKey).

        Raises:
            OssError: On access-denied, server, or network errors —
                these are infrastructure failures, not "object missing".
        """
        ...

    def generate_download_url(
        self,
        staging_path: str,
        expire_seconds: int,
        response_params: dict | None = None,
    ) -> str:
        """Generate a presigned GET URL for downloading a file from the staging path.

        The staging_path is a complete object storage key constructed by the
        Dispatcher.  This method does NOT construct or transform the path.

        Args:
            staging_path: Complete object storage key.
            expire_seconds: URL validity duration in seconds.
            response_params: Optional dict of additional query parameters for
                the presigned URL (e.g. ``{"response-content-disposition": "attachment"}``
                for forced download).  Default None.  Backward compatible —
                Bot callers do not pass this arg.

        Returns:
            Presigned GET URL string.
        """
        ...

    def initiate_multipart_upload(
        self, staging_path: str, expire_seconds: int, part_count: int = 2
    ) -> MultipartSession:
        """Kick off multipart upload.

        part_count drives how many pre-signed part URLs are returned;
        default 2 for stub compatibility, real callers pass
        ceil(file_size / part_size).  Returns session with all part
        pre-signed URLs.

        Args:
            staging_path: Complete object storage key.
            expire_seconds: URL validity duration in seconds.
            part_count: Number of parts to generate pre-signed URLs for.

        Returns:
            MultipartSession with session_id and per-part upload URLs.
        """
        ...

    def list_parts(self, staging_path: str, session_id: str) -> list[PartInfo]:
        """Query the storage backend for uploaded parts.

        Returns {part_number, etag} per part.  Used by the Dispatcher
        before completing a multipart upload to validate part count
        completeness.

        Args:
            staging_path: Complete object storage key.
            session_id: Upload session identifier for the multipart session.

        Returns:
            List of PartInfo with etag populated from the storage backend response.
        """
        ...

    def complete_multipart_upload(
        self, staging_path: str, session_id: str, parts: list[PartInfo]
    ) -> None:
        """Assemble multipart upload.

        The parts list is sourced from list_parts (not from
        callers); the Dispatcher self-queries uploaded parts, so callers
        never need to collect ETags.  The Dispatcher is responsible for
        validating part count completeness before calling this method.

        Args:
            staging_path: Complete object storage key.
            session_id: Upload session identifier.
            parts: List of PartInfo from list_parts (with etag set).
        """
        ...

    def abort_multipart_upload(self, staging_path: str, session_id: str) -> None:
        """Cancel an in-progress multipart upload.

        Aborts the multipart upload session, freeing any uploaded parts.

        Args:
            staging_path: Complete object storage key.
            session_id: Upload session identifier.
        """
        ...

    def delete_object(self, key: str) -> None:
        """Delete a single object from staging.

        Hard delete — the object is permanently removed from storage.

        Args:
            key: Full object storage key to delete.
        """
        ...

    def list_objects(
        self,
        prefix: str,
        limit: int,
        marker: str | None,
    ) -> ObjectListing:
        """List staging objects with marker pagination.

        Used for staging management (e.g., listing objects under a
        tenant/subdir prefix).  ``limit`` is capped at 1000 (OSS max).

        Args:
            prefix: Object storage key prefix to filter by.
            limit: Maximum number of objects to return (capped at 1000).
            marker: Opaque pagination marker from previous response.

        Returns:
            ObjectListing with items, truncated flag, and next_marker.
        """
        ...

    def build_staging_path(
        self,
        tenant: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        """Construct full object storage key for file transfer staging.

        The Dispatcher calls this instead of hardcoding paths.
        Pattern: ``{staging_root}/{tenant}[/{subdir}]/{transfer_id}/{filename}``

        Args:
            tenant: Tenant identifier for scoping.
            transfer_id: Transfer ticket ID for uniqueness.
            filename: Target filename on the storage object.
            subdir: Optional subdirectory under the tenant scope.

        Returns:
            Complete object storage key string.
        """
        ...

    def build_session_staging_path(
        self,
        tenant: str,
        session_id: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        """Construct full object storage key for Session File Sharing staging.

        The Session Dispatcher calls this instead of hardcoding paths.
        Parallel to ``build_staging_path()`` for Bot use; does not modify
        the old method.

        Pattern:
          ``{staging_root}/{env}/{tenant}/{session_id}/[{subdir}/]{transfer_id}/{filename}``

        The ``env`` component is resolved internally by implementations via
        ``get_current_env()`` — it is NOT passed as a parameter, keeping
        the Dispatcher's call site clean.  All four input fields (tenant,
        session_id, transfer_id, filename) and the optional subdir are
        checked for path traversal (``..`` detection), and both ``subdir``
        and ``filename`` are stripped of leading/trailing slashes before
        path construction.

        Args:
            tenant: Tenant identifier for scoping.
            session_id: Owning session identifier (replaces device scope).
            transfer_id: Transfer ticket ID for uniqueness.
            filename: Target filename on the storage object.
            subdir: Optional subdirectory grouping under the session scope.

        Returns:
            Complete object storage key string for the Session staging area.
        """
        ...
