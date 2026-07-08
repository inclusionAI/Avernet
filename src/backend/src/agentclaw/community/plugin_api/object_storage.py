"""ObjectStoragePlugin — object storage operations the backend depends on.

Abstracts the small slice of object storage that backend code currently
uses. Implementations are selected by deploy profile:

- corp: ``plugins.prod.oss_storage.OSSStorageManager`` — the corp object
  store. Structurally satisfies this Protocol; bound by the corp
  object-storage module.
- community: filesystem / S3-compatible impls under ``plugins.community``.
- test: ``plugins.local.oss_storage.MockObjectStoragePlugin`` — a mock with
  reconfigurable per-method ``MagicMock`` handles.

The surface is intentionally narrow — only methods consumed by current
backend code paths. Add a method here as a new consumer lights up; do not
proactively mirror a full object-storage SDK.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class ObjectStoragePlugin(Plugin, Protocol):
    """Object storage operations the backend depends on."""

    def put_object(self, key: str, content: bytes | str) -> bool:
        """Upload ``content`` to ``key``. Return ``True`` on success.

        Implementations should swallow transport/SDK errors and return
        ``False`` rather than raising, so callers can decide policy.
        """
        ...

    def put_file(self, key: str, local_path: str) -> bool:
        """Upload a local file to ``key``. Return ``True`` on success.

        Prefer this over ``put_object`` for large files to avoid reading
        the entire file into memory.
        """
        ...

    def delete_object(self, key: str) -> bool:
        """Delete the object at ``key``. Return ``True`` on success.

        Implementations should treat an already-absent object as success
        (idempotent) and swallow transport/SDK errors into ``False`` rather
        than raising, so callers can decide policy.
        """
        ...

    def list_objects(self, prefix: str, max_keys: int = 1000) -> list[str]:
        """List the keys of every object under ``prefix``.

        Returns the object keys (not trimmed of the prefix). Used to delete a
        directory tree by enumerating its members. Implementations should swallow
        transport/SDK errors and return an empty list rather than raising.
        """
        ...

    def sign_url(self, key: str, expires: int = 7200) -> str:
        """Return a presigned GET URL for ``key`` valid for ``expires``
        seconds. Implementations may return any URL-shaped string; tests
        typically return a ``mock://...`` form.
        """
        ...

    def get_etag(self, key: str) -> str | None:
        """Return the ETag for ``key``, or ``None`` if unavailable.

        The ETag is an opaque string (typically an MD5 hash quoted by the
        object-storage SDK). Consumers should treat it as an opaque fingerprint
        and compare for exact equality only.
        """
        ...

    def set_object_acl(self, key: str, acl: str) -> bool:
        """Set the ACL for ``key`` to ``acl``.

        Common ``acl`` values: ``"private"``, ``"public-read"``,
        ``"public-read-write"``.  Return ``True`` on success.
        """
        ...

    def ensure_directory(self, directory_path: str) -> bool:
        """Ensure ``directory_path`` exists (``mkdir -p`` semantics).

        Return ``True`` on success or when the directory already exists.
        Return ``False`` on transport/SDK failure.
        """
        ...
