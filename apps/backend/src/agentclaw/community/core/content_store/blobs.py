"""Where the content store's bytes live: the blob contract and its object-storage implementation.

``ContentStoreService`` owns the address grammar, the provenance rows and the
proof on read (the hash check). It does not own the medium. A
:class:`BlobStore` is that medium: bytes in under a digest, bytes out under
the same digest, nothing else. Which implementation a deployment runs is a
wiring decision of its profile (``di/modules/content_store_module.py`` binds
the default; a profile column may override it):

- :class:`ObjectStorageBlobStore` — the deployment's object storage, under
  ``content-store/blobs/<hex[:2]>/<hex64>``. Every backend instance bound to
  the same storage reads what any of them wrote, and a blob outlives the
  instance that wrote it. The default.
- ``testing.content_blob_store.LocalBlobStore`` — a directory tree on
  local disk, for the profiles whose object storage is a test double.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from agentclaw.community.core.content_store.errors import ContentStoreFault
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectCreateResult,
    ObjectReadStatus,
    ObjectStoragePlugin,
)

#: Where the blobs sit in the object storage's key space. A constant, not a
#: setting: the keys are addresses other instances compute for themselves.
#: Whatever lifecycle rule the bucket has must not expire this prefix — the
#: store's retention is "never delete" (see the DDL).
OBJECT_KEY_PREFIX = "content-store/blobs"


@runtime_checkable
class BlobStore(Protocol):
    """Bytes under a digest. The caller has already validated the digest.

    Neither method verifies content: ``ContentStoreService.read`` hashes
    what ``read`` returns, on every medium, so a store cannot hand back
    unproven bytes by forgetting to check.
    """

    def write(self, digest: str, data: bytes) -> None:
        """Make ``data`` readable under ``digest``; a no-op when the store already holds it.

        Raises :class:`~agentclaw.community.core.content_store.errors.ContentStoreFault`
        (or ``OSError`` from a local medium) when the bytes could not be stored.
        """
        ...

    def read(self, digest: str) -> Optional[bytes]:
        """The bytes stored under ``digest``, unverified; ``None`` when the address holds nothing.

        Raises :class:`~agentclaw.community.core.content_store.errors.ContentStoreFault`
        when the medium could not answer, where it can tell that apart from
        an absent blob.
        """
        ...


def object_key(digest: str) -> str:
    """The object key of a digest: ``content-store/blobs/<hex[:2]>/<hex64>``."""
    hex_digest = digest.partition(":")[2]
    return f"{OBJECT_KEY_PREFIX}/{hex_digest[:2]}/{hex_digest}"


class ObjectStorageBlobStore:
    """The blobs in the deployment's object storage.

    Needs only the base :class:`ObjectStoragePlugin` surface (``get_etag``,
    ``put_object``, ``get_object``). When the bound plugin also has
    :class:`ImmutableObjectStorageCapability` it is used for an atomic
    create and for the three-state read; without it, ``get_object`` answers
    ``None`` for an absent object and an unreachable storage alike, so an
    outage reads as "missing" rather than as a fault.
    """

    def __init__(self, storage: ObjectStoragePlugin) -> None:
        self._storage = storage

    def write(self, digest: str, data: bytes) -> None:
        """Upload once. An object already under the key is left alone: same address, same bytes.

        The existence check comes first so that re-storing content the
        storage holds (an unchanged re-apply) uploads nothing; on an object
        store it is a metadata request. An existing object is not
        re-verified here: ``ContentStoreService.read`` proves every read.

        With the immutable capability the upload is an atomic create, and a
        failed create stays a failure: a plain put on the same key is not
        atomic on every storage, and a partial object would then pass the
        existence check forever. Without the capability the put is all there
        is; replacing is harmless because every writer of an address writes
        the same bytes.
        """
        key = object_key(digest)
        if self._storage.get_etag(key) is not None:
            return
        if isinstance(self._storage, ImmutableObjectStorageCapability):
            stored = self._storage.create_object_if_absent(key, data) is not ObjectCreateResult.FAILED
        else:
            stored = self._storage.put_object(key, data)
        if not stored:
            raise ContentStoreFault(f"the object storage did not store the blob: {digest}")

    def read(self, digest: str) -> Optional[bytes]:
        key = object_key(digest)
        if not isinstance(self._storage, ImmutableObjectStorageCapability):
            return self._storage.get_object(key)
        result = self._storage.read_object(key)
        if result.status is ObjectReadStatus.NOT_FOUND:
            return None
        if result.status is ObjectReadStatus.FAILED or result.content is None:
            raise ContentStoreFault(f"the object storage could not be read for the blob: {digest}")
        return result.content
