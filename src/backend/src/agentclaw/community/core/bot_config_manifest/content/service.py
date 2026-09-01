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
#: way.
_URL_MAX = 2048
_CREDENTIAL_NAME_MAX = 128
_CONTENT_TYPE_MAX = 256
_MODIFIER_MAX = 1024

#: Blob IO granularity. Big enough that a 200-MiB archive is a few hundred
#: reads, small enough that「流式」stays honest on the byte cap's scale.
_CHUNK_BYTES = 1024 * 1024

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
    authority = (
        f"{parsed.host}:{parsed.port}" if parsed.port is not None else parsed.host
    )
    return f"{parsed.scheme}://{authority}{parsed.path or '/'}"


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
    ) -> StoredContentRecord:
        """Persist one fetched object as the platform's copy, with its receipt.

        Every refusal — receipt against bytes (re-hash, size), URL
        sanitization, column widths — happens **before any side effect**: a
        refused store leaves no blob and no row. The blob write is
        content-addressed and idempotent; the provenance row is always
        inserted (fetching the same digest twice is two audit events, not
        one). A repository failure *after* the blob landed is the standard
        content-addressed shape — the address is valid, the bytes verified,
        and the next store of the same digest reuses them.
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
        stored_content_type = _require_fits(
            "content_type", fetched.content_type, _CONTENT_TYPE_MAX
        )
        stored_modifier = _require_fits("modifier", modifier, _MODIFIER_MAX)
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
        """Write once, atomically; a verified existing address is a no-op.

        Same-address races converge by construction: every writer of a given
        address writes the same bytes (the address is their hash), so
        whichever ``os.replace`` lands last leaves identical content.

        The existing-address shortcut trusts a **size match**, not a full
        re-hash: truncation and appends — the corruption modes that leave a
        file claiming the same name — are caught at near-zero cost, and the
        correct bytes are then rewritten over the damage while they are in
        hand. Same-size bit rot stays for ``read()`` to detect loudly; the
        deliberate trade is that a full re-hash of a 200-MiB blob on every
        re-store would tax every audit fetch to defend against the rarest
        corruption mode.
        """
        blob = self._blob_path(digest)
        if blob.is_file() and blob.stat().st_size == len(data):
            return
        blob.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=blob.parent, prefix=".tmp-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, blob)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
