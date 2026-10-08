import oss2
from secbaas.community.bootstrap._configs import (
    ConfigError,
    FileTransferOssConfigSchema,
)
from secbaas.community.logger import get_logger
from secbaas.community.spi.file_transfer import (
    FileTransferBackend,
    MultipartSession,
    ObjectItem,
    ObjectListing,
    PartInfo,
)
from secbaas.community.spi.secret import SecretStorePlugin

log = get_logger("file_transfer")


class AliyunOssFileTransferBackend(FileTransferBackend):
    """Aliyun OSS implementation of FileTransferBackend.

    Uses two oss2.Bucket clients:
    - ``_bucket``: configured with ``config.endpoint`` (internal) for server-side
      OSS API calls (head_object, multipart operations, list_objects, delete_object).
    - ``_sign_bucket``: configured with ``config.external_endpoint`` (office) for
      generating presigned URLs returned to callers. Falls back to ``_bucket`` when
      ``external_endpoint`` is empty (single-endpoint mode).

    Endpoint and bucket resolved from config; AK/SK resolved from credentials
    when provided, otherwise via secret_store (D-08, D-09, D-10).
    """

    def __init__(
        self,
        config: FileTransferOssConfigSchema,
        secret_store: SecretStorePlugin | None = None,
        credentials: tuple[str, str] | None = None,
    ) -> None:
        """Initialize the OSS backend.

        AK/SK resolution order: ``credentials`` when provided, otherwise
        ``secret_store.get_kv_secret(config.secret_name)``.  Providing
        neither raises ConfigError.

        Args:
            config: OSS configuration parsed from the ``file_transfer_oss``
                (or aliyun-tenant) section.
            secret_store: Secret store for resolving AK/SK via
                ``get_kv_secret(secret_name)`` (main site).
            credentials: Optional (access_key_id, access_key_secret) tuple
                injected from the environment (aliyun tenant).
        """
        self._config = config
        if credentials is not None:
            access_key_id, access_key_secret = credentials
        elif secret_store is not None:
            resolved = secret_store.get_kv_secret(config.secret_name)
            access_key_id, access_key_secret = resolved
        else:
            raise ConfigError(
                "AliyunOssFileTransferBackend requires either credentials "
                "or secret_store"
            )
        auth = oss2.Auth(access_key_id, access_key_secret)
        self._bucket = oss2.Bucket(auth, config.endpoint, config.bucket_name)

        if config.external_endpoint:
            self._sign_bucket = oss2.Bucket(
                auth, config.external_endpoint, config.bucket_name
            )
        else:
            self._sign_bucket = self._bucket  # fallback: single-endpoint mode

    def build_staging_path(
        self,
        tenant: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        # Path traversal guard: reject components containing ".."
        for name, value in [
            ("tenant", tenant),
            ("transfer_id", transfer_id),
            ("subdir", subdir),
            ("filename", filename),
        ]:
            if value is not None and ".." in value:
                raise ValueError(f"Path traversal detected in {name}: {value!r}")
        root = self._config.staging_root_path.rstrip("/")
        subdir_part = f"{subdir}/" if subdir else ""
        return f"{root}/{tenant}/{subdir_part}{transfer_id}/{filename}"

    def build_staging_prefix(self, tenant: str, subdir: str | None = None) -> str:
        # Path traversal guard: reject components containing ".."
        for name, value in [("tenant", tenant), ("subdir", subdir)]:
            if value is not None and ".." in value:
                raise ValueError(f"Path traversal detected in {name}: {value!r}")
        root = self._config.staging_root_path.rstrip("/")
        subdir_part = f"{subdir}/" if subdir else ""
        return f"{root}/{tenant}/{subdir_part}"

    def build_session_staging_path(
        self,
        tenant: str,
        session_id: str,
        transfer_id: str,
        filename: str,
        subdir: str | None = None,
    ) -> str:
        """Construct OSS object key for Session file transfer staging.

        Pattern:
        ``{staging_root}/{tenant}/{session_id}/[{subdir}/]{transfer_id}/{filename}``

        Args:
            tenant: Tenant identifier for scoping.
            session_id: Session identifier for scoping within the tenant.
            transfer_id: Transfer ticket ID for uniqueness.
            filename: Target filename on the OSS object.
            subdir: Optional subdirectory under the session scope.

        Returns:
            Complete OSS object key string.

        Raises:
            ValueError: If any input field contains ``..`` (path traversal).
        """
        # Path traversal guard: reject components containing ".."
        for name, value in [
            ("tenant", tenant),
            ("session_id", session_id),
            ("transfer_id", transfer_id),
            ("subdir", subdir),
            ("filename", filename),
        ]:
            if value is not None and ".." in value:
                raise ValueError(f"Path traversal detected in {name}: {value!r}")
        root = self._config.staging_root_path.rstrip("/")
        subdir_part = f"{subdir}/" if subdir else ""
        return f"{root}/{tenant}/{session_id}/{subdir_part}{transfer_id}/{filename}"

    def generate_upload_url(
        self,
        staging_path: str,
        expire_seconds: int,
        content_type: str | None = None,
    ) -> str:
        try:
            if content_type is not None:
                return self._sign_bucket.sign_url(
                    "PUT",
                    staging_path,
                    expire_seconds,
                    headers={"Content-Type": content_type},
                )
            return self._sign_bucket.sign_url("PUT", staging_path, expire_seconds)
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in generate_upload_url: %s (code=%s)",
                str(e),
                getattr(e, "code", "N/A"),
            )
            raise

    def check_object_exists(self, staging_path: str) -> bool:
        try:
            self._bucket.head_object(staging_path)
            return True
        except oss2.exceptions.NoSuchKey:
            return False
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error checking object existence at %s: %s (code=%s)",
                staging_path,
                str(e),
                getattr(e, "code", "N/A"),
            )
            raise

    def generate_download_url(
        self,
        staging_path: str,
        expire_seconds: int,
        response_params: dict | None = None,
    ) -> str:
        try:
            sign_kwargs: dict = {}
            if response_params:
                sign_kwargs["params"] = response_params
            return self._sign_bucket.sign_url(
                "GET", staging_path, expire_seconds, **sign_kwargs
            )
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in generate_download_url: %s (code=%s)",
                str(e),
                getattr(e, "code", "N/A"),
            )
            raise

    # ── Phase 72: Multipart upload methods ────────────────────────────

    def initiate_multipart_upload(
        self,
        staging_path: str,
        expire_seconds: int,
        part_count: int = 2,
        content_type: str | None = None,
    ) -> MultipartSession:
        """Initiate OSS multipart upload and generate pre-signed per-part URLs.

        part_count drives how many pre-signed part URLs are returned.
        Default 2 for stub compatibility; real callers pass
        ceil(file_size / part_size).

        Args:
            staging_path: Complete OSS object key.
            expire_seconds: URL validity duration in seconds.
            part_count: Number of parts to generate pre-signed URLs for.
            content_type: Optional MIME type to include in per-part
                pre-signed signatures.

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
                sign_kwargs: dict = {
                    "params": {"uploadId": session_id, "partNumber": str(i)},
                }
                if content_type:
                    sign_kwargs["headers"] = {"Content-Type": content_type}
                upload_url = self._sign_bucket.sign_url(
                    "PUT", staging_path, expire_seconds, **sign_kwargs
                )
                parts.append(PartInfo(part_number=i, upload_url=upload_url))

            log.info(
                "[file-transfer:initiate_multipart_upload] result: session_id=%s, part_count=%s",
                session_id,
                part_count,
            )
            return MultipartSession(
                session_id=session_id,
                part_count=part_count,
                parts=parts,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in initiate_multipart_upload: %s (code=%s)",
                str(e),
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
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in list_parts: %s (code=%s)",
                str(e),
                getattr(e, "code", "N/A"),
            )
            raise

    def complete_multipart_upload(
        self,
        staging_path: str,
        session_id: str,
        parts: list[PartInfo],
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
        try:
            # Pitfall 2: oss2 expects oss2.models.PartInfo with part_number and etag
            oss_parts = [oss2.models.PartInfo(p.part_number, p.etag) for p in parts]
            self._bucket.complete_multipart_upload(
                staging_path,
                session_id,
                oss_parts,
            )
            log.info(
                "[file-transfer:complete_multipart_upload] result: done, part_count=%s",
                len(oss_parts),
            )
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in complete_multipart_upload: %s (code=%s)",
                str(e),
                getattr(e, "code", "N/A"),
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
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in abort_multipart_upload: %s (code=%s)",
                str(e),
                getattr(e, "code", "N/A"),
            )
            raise

    # ── Phase 72: Staging object management methods ───────────────────

    def list_objects(
        self,
        prefix: str,
        limit: int,
        marker: str | None,
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
                len(items),
                result.is_truncated,
            )
            return ObjectListing(
                items=items,
                truncated=result.is_truncated,
                next_marker=result.next_marker if result.is_truncated else None,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in list_objects: %s (code=%s)",
                str(e),
                getattr(e, "code", "N/A"),
            )
            raise

    def delete_object(self, key: str) -> None:
        """Delete a single object from staging.

        Hard delete — the object is permanently removed from OSS.

        Tolerates ``NoSuchKey`` gracefully: OSS lifecycle policies may have
        already cleaned up the object before the ticket is deleted.  In that
        case the delete is a no-op (idempotent).

        Args:
            key: Full OSS object key to delete.
        """
        try:
            self._bucket.delete_object(key)
            log.info("[file-transfer:delete_object] result: done")
        except oss2.exceptions.NoSuchKey:
            log.info(
                "[file-transfer:delete_object] object already gone (NoSuchKey): %s",
                key,
            )
        except (oss2.exceptions.OssError, oss2.exceptions.ClientError) as e:
            log.error(
                "OSS error in delete_object: %s (code=%s)",
                str(e),
                getattr(e, "code", "N/A"),
            )
            raise
