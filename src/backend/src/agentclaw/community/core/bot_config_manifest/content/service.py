"""The manifest content store — platform-side copies of fetched sources (W11, #1510).

The §2.8 hard requirement: content the platform fetches on a manifest's
behalf is kept as **the platform's own durable copy**, and every step after
fetch reads that copy. This service is the one mechanism behind all three
consumers of the requirement — audit (what did this bot receive, from
where, when), delivery (a retried apply re-reads here, never re-fetches),
and ``keep_last`` (W4's per-entry fallback is a digest the orchestrator
remembers plus a read from here; one store, one addressing, no second copy).

Two halves:

- **Bytes** live in a content-addressed blob directory —
  ``<root>/blobs/<hex[:2]>/<hex64>``. The digest (``sha256:<hex>``, the
  fetcher's own vocabulary) IS the address; identical bytes are written
  once, ever. Writing is atomic (temp + ``os.replace`` in the same
  directory), and reading verifies the hash on the same pass — the store
  hands back bytes it can prove, or it fails.
- **Provenance** lives in ``ac_manifest_content`` (append-only, one row per
  store event) via :class:`ManifestContentRepositoryProtocol`.

Retention, stated against the audit requirement rather than picked: v1
retains rows and blobs unconditionally — no delete, no sweep, no TTL. Until
an audit horizon is named, any deletion is a manufactured audit gap. A
retention window, when audit names one, is a DDL-comment change plus a
sweep mechanism — never a silent default.

Credentials never enter this layer's bytes or logs: ``store`` takes a
credential **name** (W3's identifier) and records it as a name; the
secret's value is ciphertext in another table and never crosses here.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from datetime import timezone
from pathlib import Path
from typing import Optional

import httpx

from agentclaw.community.core.bot_config_manifest.content.errors import (
    ContentIntegrityError,
    ContentMissingError,
    ContentStoreError,
)
from agentclaw.community.core.bot_config_manifest.content.models import (
    ContentScope,
    StoredContentRecord,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchedObject,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import DIGEST_RE
from agentclaw.community.core.repository.protocols.bot import (
    DEFAULT_RECORD_LIMIT,
    ManifestContentRepositoryProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: Column widths of ``ac_manifest_content``, enforced in ``store`` **before
#: the blob is written**. Source-side wire data (a Content-Type header, a
#: redirect Location) has no meaningful length cap of its own, and the
#: SQLite of tests does not enforce varchar widths — without this check an
#: oversized value would fail (strict mode) or be silently truncated
#: (non-strict, unfixable forever) at the row insert, after the blob is
#: already on disk. §2.8's audit trail must not learn about width the hard
#: way. (``content_type`` is the one exemption — advisory, see
#: ``_advisory_content_type``.)
_URL_MAX = 2048
_CREDENTIAL_NAME_MAX = 128
_CONTENT_TYPE_MAX = 256
_MODIFIER_MAX = 1024
#: The apply-linkage trio from the DDL — same before-the-blob discipline.
_APPLY_ID_MAX = 64
_CATEGORY_MAX = 32
_ENTRY_IDENTITY_MAX = 256

#: Blob IO granularity. Big enough that a 200-MiB archive is a few hundred
#: reads, small enough that「流式」stays honest on the byte cap's scale.
_CHUNK_BYTES = 1024 * 1024

#: How old a ``.tmp-*`` staging leftover must be before a store into the same
#: shard collects it — old enough that no live concurrent writer owns it,
#: young enough that a crashed write's 200-MiB orphan cannot sit on a NAS for
#: months. The one sanctioned exception to the no-delete retention (see the
#: DDL).
_TMP_SWEEP_AGE_S = 3600.0

#: Where the blob tree sits under the configured root.
_BLOBS_DIRNAME = "blobs"


def _require_valid_digest(digest: str) -> str:
    """The address must be a well-formed digest — refuse before any IO."""
    if not isinstance(digest, str) or not DIGEST_RE.match(digest):
        raise ContentStoreError(f"untrusted content address: {digest!r}")
    return digest


def _require_fits(field: str, value: Optional[str], width: int) -> Optional[str]:
    """A value headed for a varchar column must fit it — before the blob write.

    The URL lengths are checked *after* sanitization (a signed query string is
    dropped there), so what is measured is exactly what would be stored.
    """
    if value is None:
        return None
    if len(value) > width:
        raise ContentStoreError(
            f"provenance {field} exceeds the {width}-char column: length "
            f"{len(value)}"
        )
    return value


def _sanitized_url(url: str) -> str:
    """A URL fit for an append-only audit log: scheme://host[:port]/path.

    Userinfo is refused by the fetcher before any wire contact, so it never
    legitimately appears here; a query string is where signed-source tokens
    live — the same reason W2 logs host-only. The reconciliation anchor for
    audit is the digest, never a one-time signed URL. Fragments carry no
    request meaning and are dropped by construction.

    IPv6 literals keep their brackets: ``httpx.URL.host`` returns the
    address *bare*, and a reassembled ``scheme://2001:db8::1:8443/...``
    makes the port ambiguous and the row unreadable — in a table that is
    never corrected after write. Test-pinned.

    One fidelity note, accepted for v1: ``httpx.URL`` renders the host
    (punycode-decoded IDN) and path (percent-decoded) in its canonical
    form, so what lands here is that rendering rather than the exact wire
    bytes — the digest remains the byte-true anchor, so this is cosmetic
    for audit, never for reconciliation.

    Malformed input raises — but the message never echoes the URL itself: a
    URL exotic enough to defeat the parser may carry a signed query string,
    and this family's messages go to logs and (eventually, W4) error
    surfaces. Length and cause only.
    """
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError) as exc:
        raise ContentStoreError(
            f"unparseable provenance URL: length {len(url)}"
        ) from exc
    if not parsed.scheme or not parsed.host:
        raise ContentStoreError(
            f"unparseable provenance URL: length {len(url)}"
        )
    # httpx yields IPv6 hosts unbracketed; re-bracket before reassembling.
    host = f"[{parsed.host}]" if ":" in parsed.host else parsed.host
    authority = f"{host}:{parsed.port}" if parsed.port is not None else host
    return f"{parsed.scheme}://{authority}{parsed.path or '/'}"


def _advisory_content_type(value: Optional[str]) -> Optional[str]:
    """Content-type as advisory metadata: over-wide stores NULL, not a refusal.

    The bytes are already fetched, streamed under the cap and digest-verified
    by the time this runs; throwing the receipt away over a 257-char header —
    nothing on the wire bounds it — would hand the source side a lever to
    erase provenance. The digest is the reconciliation anchor, not the media
    type: NULL plus a log line keeps the one and drops nothing that matters.
    (Truncation was rejected on read-back grounds: a cut media type looks
    like a real one, and advisory-NULL is honest in a way 'application/jso…'
    never is.)
    """
    if value is None or len(value) <= _CONTENT_TYPE_MAX:
        return value
    logger.warning(
        "[manifest.content] content-type header exceeded the %d-char column; "
        "storing NULL (advisory field, digest is the anchor), length=%d",
        _CONTENT_TYPE_MAX,
        len(value),
    )
    return None


class ManifestContentService:
    """Store fetched bytes once, address them by digest, read them back provably."""

    def __init__(
        self,
        repository: ManifestContentRepositoryProtocol,
        root: Path,
    ) -> None:
        """Args:
            repository: the provenance log (``ac_manifest_content``).
            root: the blob tree's root. Relative paths resolve against the
                process working directory and ``~`` expands — a deployment's
                overlay says ``/mnt/nas/...``, the neutral default says
                ``./data/manifest_content``, and both arrive here as a Path.
        """
        self._repository = repository
        expanded = Path(root).expanduser()
        self._blobs = (
            expanded if expanded.is_absolute() else Path.cwd() / expanded
        ) / _BLOBS_DIRNAME

    def store(
        self,
        fetched: FetchedObject,
        *,
        scope: ContentScope,
        source_url: str,
        credential_name: Optional[str] = None,
        modifier: str = "",
        apply_id: Optional[str] = None,
        category: Optional[str] = None,
        entry_identity: Optional[str] = None,
    ) -> StoredContentRecord:
        """Persist one fetched object as the platform's copy, with its receipt.

        Every refusal — receipt against bytes (re-hash, size), URL
        sanitization, column widths — happens **before any side effect**: a
        refused store leaves no blob and no row. The one exception is
        ``content_type``, which is advisory metadata: a header wider than the
        column stores ``NULL`` plus a log line rather than throwing away
        verified bytes — the digest is the reconciliation anchor, not the
        media type. The blob write is content-addressed, idempotent, and
        durable before visible; the provenance row is always inserted
        (fetching the same digest twice is two audit events, not one). A
        repository failure *after* the blob landed is the standard
        content-addressed shape — the address is valid, the bytes verified,
        and the next store of the same digest reuses them.

        ``apply_id`` / ``category`` / ``entry_identity`` are the row's link
        back to the apply and the entry the fetch served. Optional because
        this layer should not *require* what its caller may not know
        (keep_last reuse, hand-driven fetches); when they arrive they make
        "what did apply X fetch" and "what was materialised for this entry"
        indexed reads instead of JSON-blob archaeology.
        """
        digest = _require_valid_digest(fetched.sha256)
        computed = "sha256:" + hashlib.sha256(fetched.bytes).hexdigest()
        if computed != digest:
            raise ContentIntegrityError(
                "receipt does not match the bytes it came with"
            )
        if len(fetched.bytes) != fetched.size_bytes:
            raise ContentIntegrityError(
                "declared size does not match the bytes it came with"
            )
        # Sanitized then measured: what is stored is what was checked.
        sanitized_source = _require_fits(
            "source_url", _sanitized_url(source_url), _URL_MAX
        )
        sanitized_fetched = _require_fits(
            "fetched_url", _sanitized_url(fetched.url), _URL_MAX
        )
        stored_credential = _require_fits(
            "credential_name", credential_name, _CREDENTIAL_NAME_MAX
        )
        stored_content_type = _advisory_content_type(fetched.content_type)
        stored_modifier = _require_fits("modifier", modifier, _MODIFIER_MAX)
        stored_apply_id = _require_fits("apply_id", apply_id, _APPLY_ID_MAX)
        stored_category = _require_fits("category", category, _CATEGORY_MAX)
        stored_identity = _require_fits(
            "entry_identity", entry_identity, _ENTRY_IDENTITY_MAX
        )
        fetched_at = fetched.fetched_at
        if fetched_at.tzinfo is not None:
            fetched_at = fetched_at.astimezone(timezone.utc).replace(tzinfo=None)
            # The column is naive by platform convention (gmt_* are too);
            # FetchedObject is tz-aware, so normalize once, here — the one
            # place the two vocabularies meet.
        record = StoredContentRecord(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            digest=digest,
            source_url=sanitized_source,
            fetched_url=sanitized_fetched,
            credential_name=stored_credential,
            content_type=stored_content_type,
            size_bytes=fetched.size_bytes,
            fetched_at=fetched_at,
            apply_id=stored_apply_id,
            category=stored_category,
            entry_identity=stored_identity,
            modifier=stored_modifier,
        )
        self._write_blob(digest, fetched.bytes)
        stored = self._repository.add(record)
        # Host only, never the URL: query strings are where signed-source
        # tokens live — same posture as the fetcher's own log line.
        logger.info(
            "[manifest.content] blob ready host=%s digest=%s bytes=%s",
            httpx.URL(fetched.url).host,
            digest,
            fetched.size_bytes,
        )
        return stored

    def read(self, digest: str) -> bytes:
        """The one read path, shared by delivery and audit (§2.8).

        The blob is read in chunks with the hash computed on the same pass,
        and returned whole: the store hands back bytes it can prove, or it
        raises — a re-delivery that "mostly" matches its address would
        defeat the receipt contract exactly where it matters. A missing
        address is terminal (``ContentMissingError``); this layer never
        re-fetches.

        Whole-bytes on purpose, at a stated cost: at the schema §5 cap
        (100–200 MiB an entry) the peak is ~2× the blob (the chunk list and
        the joined return). Apply is rare, and the read's consumer — W4's
        delivery — materialises the full payload into the artifact anyway;
        a chunk-wise contract is a decision for when a consumer exists that
        wants one, not a default to speculate at now.
        """
        digest = _require_valid_digest(digest)
        blob = self._blob_path(digest)
        if not blob.is_file():
            raise ContentMissingError(f"no stored content for digest: {digest}")
        hasher = hashlib.sha256()
        chunks: list[bytes] = []
        with open(blob, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_BYTES)
                if not chunk:
                    break
                hasher.update(chunk)
                chunks.append(chunk)
        if "sha256:" + hasher.hexdigest() != digest:
            raise ContentIntegrityError(f"stored blob fails its own digest: {digest}")
        return b"".join(chunks)

    def records(
        self,
        scope: ContentScope,
        *,
        limit: Optional[int] = None,
    ) -> list[StoredContentRecord]:
        """The audit read: one bot's receipts, newest first."""
        return self._repository.records_for(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            limit=DEFAULT_RECORD_LIMIT if limit is None else limit,
        )

    # --- the blob tree ----------------------------------------------------

    def _blob_path(self, digest: str) -> Path:
        hex_digest = digest.partition(":")[2]
        return self._blobs / hex_digest[:2] / hex_digest

    def _write_blob(self, digest: str, data: bytes) -> None:
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
        audit read path pays the full hash unconditionally, in ``read``).
        Truncation and appends — the corruption modes that leave a file
        claiming the same name, including a torn write survived from an
        unfsynced predecessor — are caught at near-zero cost, and the correct
        bytes are then rewritten over the damage while they are in hand.
        Same-size bit rot stays for ``read`` to detect loudly. Note the
        boundary this leaves: on this shortcut ``store`` succeeds and files
        its provenance row **without re-verifying the bytes already on
        disk** — "hands back bytes it can prove, or fails" is a ``read``
        guarantee; ``store`` guarantees what it wrote itself, when it wrote.
        """
        blob = self._blob_path(digest)
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
