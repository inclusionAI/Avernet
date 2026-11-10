"""Where the content store's bytes live: the blob seam and its implementations.

``ContentStoreService`` owns the address grammar, the provenance rows and the
proof on read (the hash check). It does not own the medium. A
:class:`BlobStore` is that medium: bytes in under a digest, bytes out under
the same digest, nothing else.

- :class:`LocalBlobStore` — a content-addressed directory tree,
  ``<root>/blobs/<hex[:2]>/<hex64>``. One process root; visible to another
  backend instance only when the root is a volume both mount.
- :class:`ObjectStorageBlobStore` — the deployment's object storage, under
  ``content-store/blobs/<hex[:2]>/<hex64>``. Every backend instance bound to
  the same storage reads what any of them wrote, and a blob outlives the
  instance that wrote it. It can read through to a :class:`LocalBlobStore`
  holding what an earlier version stored on this instance's disk.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Optional, Protocol

from agentclaw.community.core.content_store.errors import ContentStoreFault
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import (
    ImmutableObjectStorageCapability,
    ObjectCreateResult,
    ObjectReadStatus,
    ObjectStoragePlugin,
)

logger = get_logger()

#: How old a ``.tmp-*`` staging leftover must be before a store into the same
#: shard collects it — old enough that no live concurrent writer owns it,
#: young enough that a crashed write's 200-MiB orphan cannot sit on a NAS for
#: months. The one sanctioned exception to the no-delete retention (see the
#: DDL).
_TMP_SWEEP_AGE_S = 3600.0

#: Where the blob tree sits under the configured root.
_BLOBS_DIRNAME = "blobs"

#: Where the blobs sit in the object storage's key space. A constant, not a
#: setting: the keys are addresses other instances compute for themselves.
#: Whatever lifecycle rule the bucket has must not expire this prefix — the
#: store's retention is "never delete" (see the DDL).
OBJECT_KEY_PREFIX = "content-store/blobs"


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


class LocalBlobStore:
    """The blob tree on a local (or mounted) directory."""

    def __init__(self, root: Path) -> None:
        """Args:
            root: the blob tree's root. Relative paths resolve against the
                process working directory and ``~`` expands.
        """
        expanded = Path(root).expanduser()
        self.root = (
            expanded if expanded.is_absolute() else Path.cwd() / expanded
        ) / _BLOBS_DIRNAME

    def path(self, digest: str) -> Path:
        hex_digest = digest.partition(":")[2]
        return self.root / hex_digest[:2] / hex_digest

    def read(self, digest: str) -> Optional[bytes]:
        blob = self.path(digest)
        if not blob.is_file():
            return None
        return blob.read_bytes()

    def write(self, digest: str, data: bytes) -> None:
        """Write once, atomically and **durable before visible**.

        Same-address races converge by construction: every writer of a given
        address writes the same bytes (the address is their hash), so
        whichever ``os.replace`` lands last leaves identical content.

        The data is fsynced **before** the rename: ``os.replace`` buys the
        atomicity of the name change, not the durability of the bytes, and a
        power loss between the two would otherwise leave the address visible
        holding zero or partial bytes — exactly the corruption this layer
        exists to make impossible, and one an in-flight retry cannot heal
        because this store never re-fetches. (The directory entry itself is
        not fsynced; that is the accepted residue — a lost link loses the
        *name*, and the bytes are re-written by the next store of the same
        content, which dedup re-runs by size.)

        The existing-address shortcut trusts a **size match**, not a full
        re-hash — a stat instead of a read, on this dedup path only (the
        audit read path pays the full hash unconditionally, in
        ``ContentStoreService.read``). Truncation and appends — the
        corruption modes that leave a file claiming the same name, including
        a torn write survived from an unfsynced predecessor — are caught at
        near-zero cost, and the correct bytes are then rewritten over the
        damage while they are in hand. Same-size bit rot stays for ``read``
        to detect loudly. Note the boundary this leaves: on this shortcut
        ``store`` succeeds and files its provenance row **without
        re-verifying the bytes already on disk** — "hands back bytes it can
        prove, or fails" is a ``read`` guarantee; ``store`` guarantees what
        it wrote itself, when it wrote.
        """
        blob = self.path(digest)
        self._sweep_stale_tmp_files(blob.parent)
        if blob.is_file() and blob.stat().st_size == len(data):
            return
        blob.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=blob.parent, prefix=".tmp-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, blob)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _sweep_stale_tmp_files(self, shard: Path) -> None:
        """Collect ``.tmp-*`` leftovers from crashed writes in one shard.

        The only thing the otherwise absolute no-delete retention permits
        (stated in the DDL): a temp file is staging that never became an
        audit fact. Age-based, not eager — a *recent* tmp may belong to a
        concurrent store in another process sharing the root, so only files
        older than :data:`_TMP_SWEEP_AGE_S` go; a crashed write's leftovers
        are collected by the next store into the same shard, and shards that
        are never written again keep their orphans until an operator names an
        audit horizon for the blob layer — the exit was deliberately left
        visible rather than speculative.
        """
        if not shard.is_dir():
            return
        now = time.time()
        for leftover in shard.glob(".tmp-*"):
            try:
                age = now - leftover.stat().st_mtime
            except OSError:
                continue
            if age >= _TMP_SWEEP_AGE_S:
                leftover.unlink(missing_ok=True)


def object_key(digest: str) -> str:
    """The object key of a digest: ``content-store/blobs/<hex[:2]>/<hex64>``, the local tree's layout."""
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

    ``legacy`` is the local tree an earlier version of this store wrote to.
    A blob the object storage does not hold is looked up there, and one
    that proves its digest is copied forward, so content stored before the
    move stays readable wherever the tree survives.
    """

    def __init__(self, storage: ObjectStoragePlugin, *, legacy: Optional[LocalBlobStore] = None) -> None:
        self._storage = storage
        self._legacy = legacy

    def write(self, digest: str, data: bytes) -> None:
        """Upload once. An object already under the key is left alone: same address, same bytes.

        The existence check is one metadata request, so re-storing content
        the storage holds (an unchanged re-apply) uploads nothing. An
        existing object is not re-verified here; ``ContentStoreService.read``
        proves every read.
        """
        key = object_key(digest)
        if self._storage.get_etag(key) is not None:
            return
        if isinstance(self._storage, ImmutableObjectStorageCapability):
            stored = self._storage.create_object_if_absent(key, data) is not ObjectCreateResult.FAILED
        else:
            # Replacing is harmless: every writer of an address writes the same bytes.
            stored = self._storage.put_object(key, data)
        if not stored:
            raise ContentStoreFault(f"the object storage did not store the blob: {digest}")

    def read(self, digest: str) -> Optional[bytes]:
        data = self._read_object(digest)
        if data is not None or self._legacy is None:
            return data
        data = self._legacy.read(digest)
        if data is None:
            return None
        if "sha256:" + hashlib.sha256(data).hexdigest() == digest:
            self._copy_forward(digest, data)
        return data

    def _read_object(self, digest: str) -> Optional[bytes]:
        key = object_key(digest)
        if not isinstance(self._storage, ImmutableObjectStorageCapability):
            return self._storage.get_object(key)
        result = self._storage.read_object(key)
        if result.status is ObjectReadStatus.FAILED:
            raise ContentStoreFault(f"the object storage could not be read for the blob: {digest}")
        if result.status is ObjectReadStatus.NOT_FOUND:
            return None
        return result.content if result.content is not None else b""

    def _copy_forward(self, digest: str, data: bytes) -> None:
        """Best effort: the read in hand has its bytes either way, and the next read tries again."""
        try:
            self.write(digest, data)
        except ContentStoreFault:
            logger.warning(
                "[manifest.content] a blob read from the local tree could not be copied "
                "to the object storage, digest=%s",
                digest,
            )
            return
        logger.info("[manifest.content] copied a blob from the local tree to the object storage, digest=%s", digest)
