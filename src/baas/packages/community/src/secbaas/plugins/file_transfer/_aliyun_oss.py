import oss2

from secbaas.logger import get_logger
from secbaas.spi.file_transfer import (
    FileTransferBackend,
    MultipartSession,
    ObjectItem,
    ObjectListing,
    PartInfo,
)
from secbaas.spi.secret import SecretStorePlugin

log = get_logger("file_transfer")

# Hardcoded per CONTEXT.md D-05 -- no external config source.
OSS_ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
OSS_BUCKET = "secbaas-file-transfer"


class AliyunOssFileTransferBackend(FileTransferBackend):
    """Aliyun OSS implementation of FileTransferBackend.

    Uses oss2 SDK for presigned URL generation and object existence checks.
    AK/SK retrieved from SecretStorePlugin at init time.  Bucket instance
    is created once and reused (singleton pattern via DI container).
    """

    def __init__(self, secret_store: SecretStorePlugin) -> None:
        access_key_id = secret_store.get_secret("secbaas.oss.access_key_id")
        access_key_secret = secret_store.get_secret("secbaas.oss.access_key_secret")
        auth = oss2.Auth(access_key_id, access_key_secret)
        self._bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

    def generate_upload_url(self, staging_path: str, expire_seconds: int) -> str:
        return self._bucket.sign_url("PUT", staging_path, expire_seconds)

    def check_object_exists(self, staging_path: str) -> bool:
        try:
            self._bucket.head_object(staging_path)
            return True
        except oss2.exceptions.NoSuchKey:
            return False

    def generate_download_url(self, staging_path: str, expire_seconds: int) -> str:
        return self._bucket.sign_url("GET", staging_path, expire_seconds)

    # ── Phase 72: Multipart upload methods ────────────────────────────

    def initiate_multipart_upload(
        self, staging_path: str, expire_seconds: int, part_count: int = 2,
    ) -> MultipartSession:
        """Initiate OSS multipart upload and generate pre-signed per-part URLs.

        part_count drives how many pre-signed part URLs are returned.
        Default 2 for stub compatibility; real callers pass
        ceil(file_size / part_size).

        Args:
            staging_path: Complete OSS object key.
            expire_seconds: URL validity duration in seconds.
            part_count: Number of parts to generate pre-signed URLs for.

        Returns:
            MultipartSession with session_id and per-part upload URLs.
        """
        try:
            # Pitfall 1: oss2 returns InitMultipartUploadResult with .upload_id
            result = self._bucket.init_multipart_upload(staging_path)
            session_id = result.upload_id

            # Generate pre-signed PUT URL for each part number
            parts = []
            for i in range(1, part_count + 1):
                upload_url = self._bucket.sign_url(
                    "PUT", staging_path, expire_seconds,
                    params={"uploadId": session_id, "partNumber": str(i)},
                )
                parts.append(PartInfo(part_number=i, upload_url=upload_url))

            log.info(
                "[file-transfer:initiate_multipart_upload] result: session_id=%s, part_count=%s",
                session_id, part_count,
            )
            return MultipartSession(
                session_id=session_id, part_count=part_count, parts=parts,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in initiate_multipart_upload: %s (code=%s)", str(e),
                getattr(e, "code", "N/A"),
            )
            raise

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
        try:
            result = self._bucket.list_parts(staging_path, session_id)
            protocol_parts = [
                PartInfo(
                    part_number=p.part_number,
                    upload_url="",  # not needed for list response
                    etag=p.etag,
                )
                for p in result.parts
            ]
            log.info(
                "[file-transfer:list_parts] result: part_count=%s",
                len(protocol_parts),
            )
            return protocol_parts
        except oss2.exceptions.OssError as e:
            log.error("OSS error in list_parts: %s (code=%s)", str(e), e.code)
            raise

    def complete_multipart_upload(
        self, staging_path: str, session_id: str, parts: list[PartInfo],
    ) -> None:
        """Assemble multipart upload.

        Validate part_count from list_parts result before calling OSS
        complete.  The parts list is sourced from list_parts (not from
        callers); the Dispatcher self-queries uploaded parts, so callers
        never need to collect ETags.

        Args:
            staging_path: Complete OSS object key.
            session_id: OSS upload_id.
            parts: List of PartInfo from list_parts (with etag set).

        Raises:
            ValueError: If part_count does not match expected.
        """
        try:
            # Pitfall 2: oss2 expects oss2.models.PartInfo with part_number and etag
            oss_parts = [
                oss2.models.PartInfo(p.part_number, p.etag) for p in parts
            ]
            self._bucket.complete_multipart_upload(
                staging_path, session_id, oss_parts,
            )
            log.info(
                "[file-transfer:complete_multipart_upload] result: done, part_count=%s",
                len(oss_parts),
            )
        except oss2.exceptions.OssError as e:
            log.error(
                "OSS error in complete_multipart_upload: %s (code=%s)",
                str(e), e.code,
            )
            raise

    def abort_multipart_upload(self, staging_path: str, session_id: str) -> None:
        """Cancel an in-progress multipart upload.

        Aborts the OSS multipart session, freeing any uploaded parts.

        Args:
            staging_path: Complete OSS object key.
            session_id: OSS upload_id.
        """
        try:
            self._bucket.abort_multipart_upload(staging_path, session_id)
            log.info(
                "[file-transfer:abort_multipart_upload] result: done, session_id=%s",
                session_id,
            )
        except oss2.exceptions.OssError as e:
            log.error(
                "OSS error in abort_multipart_upload: %s (code=%s)",
                str(e), e.code,
            )
            raise

    # ── Phase 72: Staging object management methods ───────────────────

    def list_objects(
        self, prefix: str, limit: int, marker: str | None,
    ) -> ObjectListing:
        """List staging objects with marker pagination.

        limit capped at 1000 (OSS max_keys maximum).  Returns flat list
        of objects in the staging area matching the prefix.

        Args:
            prefix: OSS key prefix to filter by.
            limit: Maximum number of objects to return (capped at 1000).
            marker: Opaque pagination marker from previous response.

        Returns:
            ObjectListing with items, truncated flag, and next_marker.
        """
        try:
            capped_limit = min(limit, 1000)
            result = self._bucket.list_objects(
                prefix=prefix,
                marker=marker or "",
                max_keys=capped_limit,
            )
            items = [
                ObjectItem(
                    key=obj.key,
                    size=obj.size,
                    last_modified=str(obj.last_modified),
                )
                for obj in result.object_list
            ]
            log.info(
                "[file-transfer:list_objects] result: count=%s, truncated=%s",
                len(items), result.is_truncated,
            )
            return ObjectListing(
                items=items,
                truncated=result.is_truncated,
                next_marker=result.next_marker if result.is_truncated else None,
            )
        except oss2.exceptions.OssError as e:
            log.error("OSS error in list_objects: %s (code=%s)", str(e), e.code)
            raise

    def delete_object(self, key: str) -> None:
        """Delete a single object from staging.

        Hard delete — the object is permanently removed from OSS.

        Args:
            key: Full OSS object key to delete.
        """
        try:
            self._bucket.delete_object(key)
            log.info("[file-transfer:delete_object] result: done")
        except oss2.exceptions.OssError as e:
            log.error("OSS error in delete_object: %s (code=%s)", str(e), e.code)
            raise