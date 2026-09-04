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

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


class ObjectCreateResult(str, Enum):
    """Outcome of an atomic create-if-absent operation."""

    CREATED = "CREATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    FAILED = "FAILED"


class ObjectReadStatus(str, Enum):
    """Distinguish a missing object from an unavailable object store."""

    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ObjectReadResult:
    status: ObjectReadStatus
    content: bytes | None = None


@runtime_checkable
class ImmutableObjectStorageCapability(Protocol):
    """Optional write-once object capability for immutable consumers.

    This is deliberately separate from :class:`ObjectStoragePlugin`: corp
    overlays that implement only the long-standing mutable surface keep
    satisfying that contract, while immutable consumers can fail closed at
    composition time until their concrete store supports conditional create
    and three-state reads.
    """

    def create_object_if_absent(
        self, key: str, content: bytes | str
    ) -> ObjectCreateResult:
        """Atomically publish the complete ``content`` without replacement."""
        ...

    def read_object(self, key: str) -> ObjectReadResult:
        """Read with distinct FOUND, NOT_FOUND, and FAILED outcomes."""
        ...


@runtime_checkable
class ObjectCopyCapability(Protocol):
    """Optional server-side copy for consumers that duplicate an object.

    Separate from :class:`ObjectStoragePlugin` for the same reason
    :class:`ImmutableObjectStorageCapability` is: an overlay that implements
    only the long-standing mutable surface keeps satisfying that contract, and
    a consumer that wants a copy checks for this one at the call site.

    A consumer must still work without it — reading the source and writing it
    back is always available — so this is an efficiency capability, not a
    correctness one. It matters where the objects are large: a copy performed
    here moves no bytes through the backend.
    """

    def copy_object(self, source_key: str, dest_key: str) -> bool:
        """Copy ``source_key`` to ``dest_key`` within the same store.

        Return ``True`` on success. Implementations should swallow
        transport/SDK errors and a missing source alike into ``False`` rather
        than raising, mirroring the rest of this module.
        """
        ...


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

    def get_object(self, key: str) -> bytes | None:
        """Return the raw bytes stored at ``key``, or ``None`` if absent.

        The read counterpart of :meth:`put_object`. Implementations should
        swallow transport/SDK errors and a missing object alike into ``None``
        rather than raising, so callers can decide policy (mirroring the rest
        of this Protocol). Decode to text at the call site when needed.
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
