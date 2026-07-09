"""Community ObjectStoragePlugin implementations.

Two real, deployable impls (not ``MockSeam`` test doubles), selected by config:

- ``CommunityFsObjectStorage`` (default) — keys map to files under a configured
  root directory. Zero dependencies, single-node "just run it".
- ``CommunityS3ObjectStorage`` — an S3-compatible client (boto3) covering MinIO,
  AWS S3, Cloudflare R2, and the Aliyun-OSS S3 endpoint.

Per the Protocol, transport/IO errors are swallowed into ``False`` / ``[]``
rather than raised.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


if TYPE_CHECKING:
    from agentclaw.community.di.config_community import CommunityS3Config


logger = get_logger()


class CommunityFsObjectStorage(ObjectStoragePlugin):
    """Object storage backed by the local filesystem under a root directory."""

    def __init__(self, root: str) -> None:
        # Resolve the root but do not create it here — construction stays
        # side-effect-free (parity with CommunityDatabase). Write paths create
        # the directories they need lazily.
        self._root = Path(root).resolve()

    def _safe_path(self, key: str) -> Path | None:
        """Resolve ``key`` to a path under the root, or ``None`` if it escapes.

        Guards against path traversal (a key like ``../etc/passwd``): the
        resolved path must be the root itself or a descendant of it.
        """
        candidate = (self._root / key).resolve()
        if candidate == self._root or self._root in candidate.parents:
            return candidate
        logger.error("ObjectStorage: key escapes storage root: %r", key)
        return None

    def put_object(self, key: str, content: bytes | str) -> bool:
        path = self._safe_path(key)
        if path is None:
            return False
        try:
            if isinstance(content, str):
                content = content.encode("utf-8")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return True
        except OSError as e:
            logger.error("ObjectStorage put_object failed: key=%s, error=%s", key, e)
            return False

    def put_file(self, key: str, local_path: str) -> bool:
        path = self._safe_path(key)
        if path is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_path, path)
            return True
        except OSError as e:
            logger.error("ObjectStorage put_file failed: key=%s, error=%s", key, e)
            return False

    def get_object(self, key: str) -> bytes | None:
        path = self._safe_path(key)
        if path is None:
            return None
        try:
            if not path.is_file():
                return None
            return path.read_bytes()
        except OSError as e:
            logger.error("ObjectStorage get_object failed: key=%s, error=%s", key, e)
            return None

    def delete_object(self, key: str) -> bool:
        path = self._safe_path(key)
        if path is None:
            return False
        try:
            # Idempotent: an already-absent object is success.
            path.unlink(missing_ok=True)
            return True
        except OSError as e:
            logger.error("ObjectStorage delete_object failed: key=%s, error=%s", key, e)
            return False

    def list_objects(self, prefix: str, max_keys: int = 1000) -> list[str]:
        try:
            if not self._root.exists():
                return []
            # Collect matching keys, then sort the posix key STRINGS (not the
            # Path objects, whose separator-aware ordering diverges from S3's
            # lexicographic key order) so max_keys truncates the same first-N
            # set the S3 backend would. Walks the whole tree — fine for a
            # single-node store.
            keys = [
                p.relative_to(self._root).as_posix()
                for p in self._root.rglob("*")
                if p.is_file()
            ]
            keys = [k for k in keys if k.startswith(prefix)]
            keys.sort()
            return keys[:max_keys]
        except OSError as e:
            logger.error("ObjectStorage list_objects failed: prefix=%s, error=%s", prefix, e)
            return []

    def sign_url(self, key: str, expires: int = 7200) -> str:
        # Single-node filesystem has no presign concept — return a file URL.
        path = self._safe_path(key)
        target = path if path is not None else (self._root / key)
        return f"file://{target}"

    def get_etag(self, key: str) -> str | None:
        path = self._safe_path(key)
        if path is None:
            return None
        try:
            if not path.is_file():
                return None
            return hashlib.md5(path.read_bytes()).hexdigest()
        except OSError as e:
            logger.error("ObjectStorage get_etag failed: key=%s, error=%s", key, e)
            return None

    def set_object_acl(self, key: str, acl: str) -> bool:
        # No ACL concept on a single-node filesystem store — no-op success.
        return True

    def ensure_directory(self, directory_path: str) -> bool:
        path = self._safe_path(directory_path)
        if path is None:
            return False
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            logger.error(
                "ObjectStorage ensure_directory failed: path=%s, error=%s",
                directory_path, e,
            )
            return False


class CommunityS3ObjectStorage(ObjectStoragePlugin):
    """Object storage over an S3-compatible service (MinIO / S3 / R2 / OSS).

    boto3 is imported lazily so a pure-filesystem community deploy that never
    installs the ``community`` dependency group can still import this module.
    Credentials come from boto3's standard env chain — never from config.
    """

    def __init__(self, cfg: "CommunityS3Config") -> None:
        import boto3
        from boto3.exceptions import S3UploadFailedError
        from botocore.exceptions import BotoCoreError, ClientError

        if not cfg.bucket:
            # Fail fast on a misconfigured deploy rather than silently logging
            # every op as a "storage failure" once requests arrive.
            raise ValueError(
                "CommunityS3ObjectStorage requires a non-empty bucket "
                "(object_storage.s3.bucket)."
            )
        # Swallow only transport/SDK errors (Protocol contract) — a narrow tuple
        # so genuine programming errors still surface, mirroring the corp impl.
        # ``upload_file`` (the high-level transfer) wraps failures in boto3's
        # ``S3UploadFailedError`` rather than a botocore error, so include it.
        self._errors: tuple[type[Exception], ...] = (
            ClientError,
            BotoCoreError,
            S3UploadFailedError,
        )
        self._bucket = cfg.bucket
        self._s3 = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint or None,
            region_name=cfg.region or None,
            use_ssl=cfg.secure,
        )

    def put_object(self, key: str, content: bytes | str) -> bool:
        try:
            if isinstance(content, str):
                content = content.encode("utf-8")
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=content)
            return True
        except self._errors as e:
            logger.error("S3 put_object failed: key=%s, error=%s", key, e)
            return False

    def put_file(self, key: str, local_path: str) -> bool:
        try:
            self._s3.upload_file(local_path, self._bucket, key)
            return True
        except self._errors as e:
            logger.error("S3 put_file failed: key=%s, error=%s", key, e)
            return False

    def get_object(self, key: str) -> bytes | None:
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except self._errors as e:
            # Includes a missing key (ClientError NoSuchKey) — swallowed to None.
            logger.error("S3 get_object failed: key=%s, error=%s", key, e)
            return None

    def delete_object(self, key: str) -> bool:
        try:
            # S3 delete is idempotent — absent keys still return success.
            self._s3.delete_object(Bucket=self._bucket, Key=key)
            return True
        except self._errors as e:
            logger.error("S3 delete_object failed: key=%s, error=%s", key, e)
            return False

    def list_objects(self, prefix: str, max_keys: int = 1000) -> list[str]:
        try:
            keys: list[str] = []
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
                    if len(keys) >= max_keys:
                        return keys
            return keys
        except self._errors as e:
            logger.error("S3 list_objects failed: prefix=%s, error=%s", prefix, e)
            return []

    def sign_url(self, key: str, expires: int = 7200) -> str:
        try:
            return self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires,
            )
        except self._errors as e:
            logger.error("S3 sign_url failed: key=%s, error=%s", key, e)
            return ""

    def get_etag(self, key: str) -> str | None:
        try:
            meta = self._s3.head_object(Bucket=self._bucket, Key=key)
            etag = meta.get("ETag")
            return etag.strip('"') if isinstance(etag, str) else None
        except self._errors as e:
            logger.error("S3 get_etag failed: key=%s, error=%s", key, e)
            return None

    def set_object_acl(self, key: str, acl: str) -> bool:
        try:
            self._s3.put_object_acl(Bucket=self._bucket, Key=key, ACL=acl)
            return True
        except self._errors as e:
            logger.error("S3 set_object_acl failed: key=%s, error=%s", key, e)
            return False

    def ensure_directory(self, directory_path: str) -> bool:
        # S3 keys are flat — there are no directories to create. The consumers
        # that call this for an OSS "directory" are satisfied by success.
        return True
