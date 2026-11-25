"""LocalBlobStore — the content store's blobs on a local directory tree (test / singlebox).

Satisfies ``core.content_store.blobs.BlobStore`` structurally. A
content-addressed tree, ``<root>/blobs/<hex[:2]>/<hex64>``, visible only to
the process (or the machine) that owns the directory — which is why it is
bound by the profiles that run one backend instance against test doubles,
and not by a deployment: there the blobs live in the object storage every
instance shares.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Optional

#: How old a ``.tmp-*`` staging leftover must be before a store into the same
#: shard collects it — old enough that no live concurrent writer owns it,
#: young enough that a crashed write's orphan cannot sit on a disk for months.
_TMP_SWEEP_AGE_S = 3600.0

#: Where the blob tree sits under the configured root.
_BLOBS_DIRNAME = "blobs"


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
