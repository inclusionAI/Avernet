"""The content store — the platform's own copies of fetched and produced bytes (W11, #1510; RSI-03).

The §2.8 hard requirement: content the platform fetches on a manifest's
behalf is kept as **the platform's own durable copy**, and every step after
fetch reads that copy. This service is the one mechanism behind all three
consumers of the requirement — audit (what did this bot receive, from
where, when), delivery (a retried apply re-reads here, never re-fetches),
and ``keep_last`` (W5's entry-fetch pipeline reads this store's newest
receipt for the entry's source and stands in with those bytes when the
source is down; one store, one addressing, no second copy).

Two halves:

- **Bytes** live in a :class:`~agentclaw.community.core.content_store.blobs.BlobStore`
  (``blobs.py``): the deployment's object storage, or a local tree under
  the profiles that run against test doubles. The digest (``sha256:<hex>``, the fetcher's
  own vocabulary) IS the address; identical bytes are written once, ever.
  Reading verifies the hash here, whatever the medium — the store hands
  back bytes it can prove, or it fails.
- **Provenance** lives in ``ac_manifest_content`` (append-only, one row per
  store event) via :class:`ContentStoreRepositoryProtocol`.

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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import httpx

from agentclaw.community.core.content_store.blobs import BlobStore
from agentclaw.community.core.content_store.errors import (
    ContentIntegrityError,
    ContentMissingError,
    ContentStoreError,
)
from agentclaw.community.core.content_store.service_protocol import (
    ContentStoreProtocol,
)
from agentclaw.community.core.content_store.models import (
    ContentScope,
    StoredContentRecord,
)
from agentclaw.community.core.content_store.limits import (
    DIGEST_RE,
    MAX_AUDIT_ACTOR_CHARS,
)
from agentclaw.community.core.repository.protocols.bot import (
    DEFAULT_RECORD_LIMIT,
    ContentStoreRepositoryProtocol,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    # The receipt type only: the store never fetches, and importing the
    # transport stack at runtime would make every consumer of the store a
    # consumer of the Manifest's fetch layer.
    from agentclaw.community.core.bot_config_manifest.fetch.errors import (
        FetchedObject,
    )

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
_MODIFIER_MAX = MAX_AUDIT_ACTOR_CHARS
#: The apply-linkage trio from the DDL — same before-the-blob discipline.
_APPLY_ID_MAX = 64
_CATEGORY_MAX = 32
_ENTRY_IDENTITY_MAX = 256


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


class ContentStoreService(ContentStoreProtocol):
    """Store fetched bytes once, address them by digest, read them back provably."""

    def __init__(
        self,
        repository: ContentStoreRepositoryProtocol,
        blobs: BlobStore,
    ) -> None:
        """Args:
            repository: the provenance log (``ac_manifest_content``).
            blobs: where the bytes live — a wiring decision of the
                deployment's profile, handed in by the composition root.
        """
        self._repository = repository
        self._blobs = blobs

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
        self._blobs.write(digest, fetched.bytes)
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

    def store_produced(
        self,
        data: bytes,
        *,
        scope: ContentScope,
        origin: str,
        modifier: str = "",
        category: Optional[str] = None,
        entry_identity: Optional[str] = None,
    ) -> StoredContentRecord:
        """Persist produced bytes: the digest is computed here, the URL is the origin label.

        Same order as ``store``: every column-width refusal happens before
        the blob write, the blob lands durably before the row, and a row the
        bot already holds for the digest is not filed twice.
        """
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        url = _require_fits("source_url", _sanitized_url(f"produced://{origin}"), _URL_MAX)
        stored_modifier = _require_fits("modifier", modifier, _MODIFIER_MAX)
        stored_category = _require_fits("category", category, _CATEGORY_MAX)
        stored_identity = _require_fits(
            "entry_identity", entry_identity, _ENTRY_IDENTITY_MAX
        )
        record = StoredContentRecord(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            digest=digest,
            source_url=url,
            fetched_url=url,
            credential_name=None,
            content_type=None,
            size_bytes=len(data),
            fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
            category=stored_category,
            entry_identity=stored_identity,
            modifier=stored_modifier,
        )
        self._blobs.write(digest, data)
        # Dedupe within the producer: a row the Manifest fetched does not stand in for the
        # registry's own, which is what makes the digest reachable through the registry.
        if self.is_reachable(scope, digest, produced_by=origin.split("/", 1)[0]):
            return record
        return self._repository.add(record)

    def ensure_stored(self, data: bytes) -> str:
        """The blob write alone: the same idempotent write ``store_produced`` makes, with no row."""
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        self._blobs.write(digest, data)
        return digest

    def is_reachable(self, scope: ContentScope, digest: str, *, produced_by: Optional[str] = None) -> bool:
        """A provenance row for this bot and digest exists (filed by ``produced_by`` when given)."""
        return self._repository.has_digest(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            digest=_require_valid_digest(digest),
            source_url_prefix=None if produced_by is None else f"produced://{produced_by}/",
        )

    def read(self, digest: str) -> bytes:
        """The one read path, shared by delivery and audit (§2.8).

        The blob store hands back whatever the address holds and this
        method hashes it, on every medium: the store hands back bytes it can
        prove, or it raises — a re-delivery that "mostly" matches its address
        would defeat the receipt contract exactly where it matters. A missing
        address is terminal (``ContentMissingError``); this layer never
        re-fetches.

        Whole-bytes on purpose, at a stated cost: at the schema §5 cap
        (100–200 MiB an entry) the blob is held in memory once. Apply is
        rare, and the read's consumer — W4's delivery — materialises the full
        payload into the artifact anyway; a chunk-wise contract is a decision
        for when a consumer exists that wants one, not a default to speculate
        at now.
        """
        digest = _require_valid_digest(digest)
        data = self._blobs.read(digest)
        if data is None:
            raise ContentMissingError(f"no stored content for digest: {digest}")
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise ContentIntegrityError(f"stored blob fails its own digest: {digest}")
        return data

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

    def latest_receipt(
        self,
        scope: ContentScope,
        *,
        source_url: str,
    ) -> Optional[StoredContentRecord]:
        """The bot's newest receipt for one source URL, or ``None``.

        The fetch pipeline's lookup — "does this bot's newest receipt for
        *this* URL hold these bytes?" — keying exactly as ``store`` keyed:
        the input is sanitized by the same rule before the exact-equality
        match, so a documented URL form the writer of a row can never store
        (a query string, a fragment) still finds the row it filed. Without
        this, signed-URL declarations would keep their platform copies but
        never consult them — re-fetching every apply and losing ``keep_last``
        for exactly the sources most likely to expire.
        """
        return self._repository.latest_for(
            env=scope.env,
            entity_id=scope.entity_id,
            bot_id=scope.bot_id,
            source_url=_sanitized_url(source_url),
        )
