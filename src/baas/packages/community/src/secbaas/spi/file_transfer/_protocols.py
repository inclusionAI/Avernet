from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class MultipartSession:
    """Result of initiating a multipart upload.

    Fields:
        session_id: OSS upload_id for the multipart session.
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
        etag: ETag returned by OSS after successful part upload. None in init response.
    """

    part_number: int
    upload_url: str
    etag: str | None = None


@dataclass(slots=True)
class ObjectItem:
    """Metadata for a single staging object returned by list_objects.

    Fields:
        key: Full OSS object key.
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
            True if object exists and is readable.
            False if object does not exist (NoSuchKey).

        Raises:
            OssError: On access-denied, server, or network errors —
                these are infrastructure failures, not "object missing".
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

    def initiate_multipart_upload(
        self, staging_path: str, expire_seconds: int, part_count: int = 2
    ) -> MultipartSession:
        """Kick off multipart upload.

        part_count drives how many pre-signed part URLs are returned;
        default 2 for stub compatibility, real callers pass
        ceil(file_size / part_size).  Returns session with all part
        pre-signed URLs.

        Args:
            staging_path: Complete OSS object key.
            expire_seconds: URL validity duration in seconds.
            part_count: Number of parts to generate pre-signed URLs for.

        Returns:
            MultipartSession with session_id and per-part upload URLs.
        """
        ...

    def list_parts(self, staging_path: str, session_id: str) -> list[PartInfo]:
        """Query OSS for uploaded parts.

        Returns {part_number, etag} per part.  Used by the Dispatcher
        before completing a multipart upload to validate part count
        completeness.

        Args:
            staging_path: Complete OSS object key.
            session_id: OSS upload_id for the multipart session.

        Returns:
            List of PartInfo with etag populated from OSS response.
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
            staging_path: Complete OSS object key.
            session_id: OSS upload_id.
            parts: List of PartInfo from list_parts (with etag set).
        """
        ...

    def abort_multipart_upload(self, staging_path: str, session_id: str) -> None:
        """Cancel an in-progress multipart upload.

        Aborts the OSS multipart session, freeing any uploaded parts.

        Args:
            staging_path: Complete OSS object key.
            session_id: OSS upload_id.
        """
        ...

    def delete_object(self, key: str) -> None:
        """Delete a single object from staging.

        Hard delete — the object is permanently removed from OSS.

        Args:
            key: Full OSS object key to delete.
        """
        ...

    def build_staging_path(
        self,
        tenant: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        """Construct full OSS object key for file transfer staging.

        The Dispatcher calls this instead of hardcoding paths.
        Pattern: ``{staging_root}/{tenant}[/{subdir}]/{transfer_id}/{filename}``

        Args:
            tenant: Tenant identifier for scoping.
            transfer_id: Transfer ticket ID for uniqueness.
            filename: Target filename on the OSS object.
            subdir: Optional subdirectory under the tenant scope.

        Returns:
            Complete OSS object key string.
        """
        ...
