"""One entry's fetch, from a declared source to materialisable bytes.

The second half of ``resolve`` for every fetch-consuming category: substitute
``${BOT_*}``, consult the platform's own copy (W11) before the network, fetch
through the guarded transport (W2) under a named credential (W3), and file the
result with the content store so delivery and audit share one copy (§2.8).

Fetch lives here **and only here** — the registry's contract says ``resolve``
is where a category's failures are collected before anything is written, and a
fetch is exactly that kind of failure. Materialisers translate :class:`EntryFetchError`
into their ``ResolveFailure`` currency; nothing about the transport leaks out.
Because the fetch belongs to ``resolve``, a ``dry_run`` may perform one (it
still writes **nothing to the bot** — the store it files with is the
platform's own record of what a bot was served, true whether or not the apply
proceeds).

Pinned vs unpinned is a declared-digest question, and it decides the
store-first vs fetch-first policy:

* **pinned** (the entry declares a ``digest``) — a receipt for this bot and
  source URL *matching the declaration* is the bytes, by content addressing:
  no network is consulted to acquire what the platform already holds. A
  source that has since moved is irrelevant — the declaration asks for the
  pinned bytes, and a re-fetch of them would only fail the pin.
* **unpinned** — the entry wants whatever is there *now*; every apply
  re-fetches so it converges to the source, and ``keep_last`` exists for the
  day that fetch fails.

``keep_last`` (on a real fetch failure) reads the latest receipt: bytes that
are entitled to be reused when the declaration pinned nothing, and bytes that
must match the pin when it did — a receipt that disagrees with a declared
digest is not "last", it is stale, and supplying it would silently pin bytes
the declaration never named.

**Which failures may fall back is fixed by class, and the ruling is:**
``FetchFailedError`` — the wire was reached and the source failed — may
fall back; ``FetchRefusedError`` and credential errors may not. A refusal
happens *before* any wire contact (non-public address, refused scheme, hop
budget, declared-digest vocabulary): it is a statement about the document's
configuration, while keep_last exists for statements about the *source's
availability*. Masking a refusal with stored bytes would answer SUCCEEDED
to a document the platform just refused on policy grounds; the same ruling
keeps a deleted credential name loud — configuration drift is for the
author to fix, not for the stored copy to absorb.

**Every interaction with the content store — lookup, both reads, the
re-file after a fetch — is translated here** into :class:`EntryFetchError`:
a store-side fault answers as that ENTRY's failure with the store's own
message, never as an unrelated exception escaping resolve to abort the
whole category under a wrapped surprise. The one leniency: a pinned
store-hit whose blob has gone missing falls through to the guarded fetch —
the pin is byte-provable, so a re-fetch re-filed with the store heals the
address and nobody upstream learns anything happened.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from agentclaw.community.core.bot_config_manifest.content.errors import (
    ContentMissingError,
    ContentStoreError,
    ContentStoreFault,
)
from agentclaw.community.core.bot_config_manifest.content.models import (
    ContentScope,
)
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
)
from agentclaw.community.core.bot_config_manifest.credentials.policy import (
    PrefixAuthorizationError,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
    FetchedObject,
    GuardedFetcher,
    FetchRequest,
)
from agentclaw.community.core.bot_config_manifest.schema import placeholders
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.apply.context import (
        ApplyContext,
    )
    from agentclaw.community.core.bot_config_manifest.content.service_protocol import (
        ManifestContentServiceProtocol,
    )
    from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (
        SourceCredentialServiceProtocol,
    )

logger = get_logger()


class EntryFetchError(Exception):
    """One entry's bytes could not be acquired, with a report-safe reason.

    The reason is built from the transport's and the credential service's own
    words — W2 refuses before sending anything that would carry caller or
    source data, and W3's error family names credentials without ever carrying
    the value — so a secret cannot ride out of this module inside an
    exception. Materialisers hand ``reason`` to ``ResolveFailure`` verbatim.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class FetchedEntry:
    """One entry's bytes, their content address, and where they came from."""

    content: bytes
    digest: str
    #: True when the platform's own copy answered — no network was touched.
    from_store: bool
    content_type: Optional[str] = None
    #: Set only when the platform's copy answered as a ``keep_last``
    #: FALLBACK — the source was fetched and failed, the stored bytes stood
    #: in. The report must say so (schema §9.6's published contract: a
    #: keep_last entry's report row states the fallback), so this carries
    #: the human-readable reason for the materialiser to surface as the
    #: entry's note. A plain store-hit (from_store, no note) is the
    #: legitimate pinned fast path and stays silent.
    fallback_reason: Optional[str] = None


def scope_of(ctx: "ApplyContext") -> ContentScope:
    """The store scope for the bot an apply runs against.

    The three axes the bot record already carries — the same scope every store
    event for this apply is filed under, so the receipts this pipeline reads
    and the ones it writes are one log.
    """
    return ContentScope(env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id)


class EntryFetcher:
    """Fetches one manifest entry's bytes on a bot's behalf.

    Composed once per apply — the transport is stateless per hop, so there is
    nothing to hold between entries — and handed to every materialiser that
    fetches. One funnel for W5's two categories and W6's ``resources`` when it
    arrives; a category that bypassed it would acquire unrecorded bytes, and
    §2.8's audit and ``keep_last`` both read from exactly this log.
    """

    def __init__(
        self,
        fetcher: GuardedFetcher,
        content: "ManifestContentServiceProtocol",
        credentials: "SourceCredentialServiceProtocol",
    ) -> None:
        self._fetcher = fetcher
        self._content = content
        self._credentials = credentials

    def fetch(
        self,
        ctx: "ApplyContext",
        *,
        source_url: str,
        digest: Optional[str] = None,
        auth: Optional[str] = None,
        category: str = "resources_file",
        keep_last: bool = False,
        entry_identity: Optional[str] = None,
    ) -> FetchedEntry:
        """Acquire one entry's bytes. Raises :class:`EntryFetchError`.

        ``${BOT_*}`` substitution happens **before** the fetch and therefore
        before prefix authorization: the W3 policy re-authorises every hop
        against the URL the request will actually name, so a substituted URL
        cannot steer the request outside its credential's prefixes (or inside
        them, unseen).

        ``entry_identity`` is the entry's own key (a skill ``name``, an
        identity ``type`` — the same way the report names entries), passed
        with the category into the receipt this files: the W11 linkage that
        makes "what was fetched for this entry" an indexed read instead of
        a ``source_url`` approximation. ``ctx.apply_id`` supplies the apply
        half; both are optional for the same reason they are on ``store``.
        """
        target = _substitute(ctx, source_url)
        scope = scope_of(ctx)
        try:
            receipt = self._content.latest_receipt(scope, source_url=target)
        except (ContentStoreError, ContentStoreFault) as exc:
            # Even the lookup is the entry's own failure with the store's own
            # message — the alternative is a raw exception escaping resolve,
            # which the orchestrator answers by aborting the WHOLE category
            # under a wrapped message nobody can act on.
            raise EntryFetchError(str(exc)) from exc

        if digest is not None and receipt is not None and receipt.digest == digest:
            # Pinned and in the platform's copy: content addressing makes the
            # stored bytes *the* declared bytes. Serving them is not a cache
            # nicety — a re-fetch of a pin can only succeed by redownloading
            # the same bytes, or fail loudly, so the store is the strictly
            # more available source of the same truth.
            try:
                return FetchedEntry(
                    content=self._content.read(digest),
                    digest=digest,
                    from_store=True,
                    content_type=receipt.content_type,
                )
            except ContentMissingError:
                # The platform's copy of the pinned bytes is gone. Fall
                # THROUGH to the guarded fetch: the pin is byte-provable, so
                # the fetch re-acquires exactly these bytes and the re-file
                # below heals the address — a self-repairing cache miss, not
                # a caller-visible failure.
                logger.warning(
                    "[manifest.fetch] the pinned blob is missing; refetching "
                    "to heal the platform's copy, digest=%s",
                    digest,
                )
            except (ContentStoreError, ContentStoreFault) as exc:
                # Present but unreadable — e.g. corrupted on disk. Not
                # healable by re-fetching (the dedup write skips same-size
                # files), so it stays what it is: platform-side damage,
                # failed loudly on this entry with its reason.
                raise EntryFetchError(
                    "the platform's copy of the pinned content could not be "
                    f"read: {exc}"
                ) from exc

        try:
            fetched = self._fetch(ctx, target=target, digest=digest, auth=auth,
                                  category=category)
        except FetchRefusedError as exc:
            # A refusal never left the wire — see the module docstring's
            # ruling. Policy and configuration are not availability, and
            # keep_last must not mask them with stored bytes: a document the
            # platform refuses on policy grounds answers today's failure,
            # not a silent SUCCEEDED out of the store.
            raise EntryFetchError(str(exc)) from exc
        except FetchFailedError as exc:
            if (
                keep_last
                and receipt is not None
                and (digest is None or receipt.digest == digest)
            ):
                logger.info(
                    "[manifest.fetch] keep_last reused the platform copy, "
                    "url_host=%s, digest=%s",
                    target.rpartition("//")[2].partition("/")[0],
                    receipt.digest,
                )
                try:
                    return FetchedEntry(
                        content=self._content.read(receipt.digest),
                        digest=receipt.digest,
                        from_store=True,
                        content_type=receipt.content_type,
                        # Visible in the report, not only in the log: the
                        # source was tried and failed, and the stored bytes
                        # stood in. The receipt's agreement with the pin
                        # (checked above) is what makes standing in
                        # legitimate; the reason is what makes it honest.
                        fallback_reason=(
                            "delivered from the platform's stored copy "
                            "(keep_last): the source fetch failed — %s" % exc
                        ),
                    )
                except (ContentStoreError, ContentStoreFault) as read_exc:
                    # Both halves, named: why the source was tried, and why
                    # the fallback could not be read either — dropping
                    # either half would leave the caller fixing the wrong
                    # thing.
                    raise EntryFetchError(
                        f"{exc}; the keep_last fallback copy could not be "
                        f"read: {read_exc}"
                    ) from read_exc
            raise EntryFetchError(str(exc)) from exc
        except CredentialError as exc:
            # A deleted or unknown credential name is configuration drift —
            # the same ruling as a policy refusal: loud, for the author to
            # fix, not absorbed by the stored copy.
            raise EntryFetchError(str(exc)) from exc
        except PrefixAuthorizationError as exc:
            # Raised per hop by the W3 policy (the initial target and every
            # redirect): the substituted URL, or a redirect, stepped outside
            # the credential's authorized prefixes. It is a refusal with a
            # report-safe reason (W3 names the credential, never the value)
            # — the entry fails, exactly like a refused address.
            raise EntryFetchError(str(exc)) from exc

        try:
            self._content.store(
                fetched,
                scope=scope,
                source_url=target,
                credential_name=auth,
                modifier=ctx.actor_id,
                apply_id=ctx.apply_id,
                category=category,
                entry_identity=entry_identity,
            )
        except (ContentStoreError, ContentStoreFault) as exc:
            # The bytes were fetched and verified but could not be filed —
            # the reachable shape: a redirect destination whose sanitized
            # form exceeds the provenance column, something admission could
            # not see. THIS entry's failure, with the store's message (which
            # never echoes the URL) — not the category's abort.
            raise EntryFetchError(
                "the fetched bytes could not be filed with the platform's "
                f"store: {exc}"
            ) from exc
        return FetchedEntry(
            content=fetched.bytes,
            digest=fetched.sha256,
            from_store=False,
            # The object's own content type is what the source served; the
            # receipt's is the width-checked column copy of the same. Either
            # may be None, and the caller's archive detection treats that as
            # "the source did not say".
            content_type=fetched.content_type,
        )

    def _fetch(
        self,
        ctx: "ApplyContext",
        *,
        target: str,
        digest: Optional[str],
        auth: Optional[str],
        category: str,
    ) -> FetchedObject:
        """One guarded request, carrying a named credential if one is declared.

        The binding object satisfies both of the fetcher's seams — headers to
        present, and per-hop re-authorization — which is W3's composition: the
        same binding refreshes from the stored row on every hop, so rotation
        lands on the very next fetch with no signal needed.
        """
        binding = self._credentials.binding(name=auth) if auth else None
        return self._fetcher.fetch(
            FetchRequest(
                url=target,
                expected_digest=digest,
                category=category,
                injector=binding,
                policy=binding,
            )
        )


def _substitute(ctx: "ApplyContext", source_url: str) -> str:
    """``${BOT_*}`` in a source URL, against this apply's deployment context.

    Unknown names are left untouched by the resolver itself — they cannot
    reach here through a stored document, because the write path refuses
    them — and a visible leftover in a fetch URL makes that bug findable
    instead of fetching something plausible-looking.
    """
    return placeholders.resolve(
        source_url,
        engine_type=ctx.engine_type,
        env=ctx.env,
        tenant=ctx.tenant,
    )


__all__ = ["EntryFetchError", "EntryFetcher", "FetchedEntry", "scope_of"]
