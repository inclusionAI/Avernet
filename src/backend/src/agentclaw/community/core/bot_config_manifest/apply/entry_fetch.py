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

W7 adds the declared-source front door, :meth:`fetch_declared`: the ``from``
and inline-source roads resolve through the apply's source session, and the git
road returns a :class:`GitEntrySource` — the tree is the entry's to
interpret (a file? a package?) — while its canonical, entry-level bytes are
filed with the store via :meth:`file_bytes`, so audit and ``keep_last`` read
the same receipts the URL roads always have.

**Dispatch is on the declared protocol**, read through the one parser the
``PUT`` validator also uses. It used to be on which key a source mapping
happened to carry, which meant this module and the validator each derived the
protocol their own way — and the digest rule derived it from a third place
again, against the source *form*, which is how ``from:`` a git source came to
be charged the object-store pin (D5).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional, Protocol, runtime_checkable


from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    BlobDelivery,
    EntryDelivery,
    EntryFetchError,
    FetchedEntry,
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.apply.source_fetchers import (
    DeclaredFetch,
    build_fetchers,
    object_receipt_url,
    substitute,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
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
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
    git_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
    FetchCategory,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
    FetchedObject,
    GuardedFetcher,
    FetchRequest,
)
from agentclaw.community.core.bot_config_manifest.schema.sources import (
    parse_source,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind
from agentclaw.community.plugin_api.object_store_client import (
    ObjectFetchStatus,
    ObjectStoreClientFactory,
    ObjectStoreTarget,
)
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.apply.budget import (
        ApplyFetchBudget,
    )
    from agentclaw.community.core.bot_config_manifest.content.service_protocol import (
        ManifestContentServiceProtocol,
    )
    from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (
        SourceCredentialServiceProtocol,
    )

logger = get_logger()


@runtime_checkable
class FetchContext(Protocol):
    """Exactly what a fetch reads off its caller's context, and nothing more.

    :class:`~agentclaw.community.core.bot_config_manifest.apply.context.ApplyContext`
    is the original and still the usual one. It is not the only one: W9's
    ``cli_tools`` service is called by an HTTP route as well as by a
    materialiser, and both must fetch through *this* funnel — a second fetch
    path is how two callers of one feature drift apart.

    Declaring the seam rather than leaving the annotation reading
    ``"ApplyContext"`` while a second type is passed makes the dependency
    honest in the one direction that matters: a maintainer who adds a
    ``ctx.something`` read below adds it here too, and the other caller fails
    to type-check instead of failing at apply time.

    ``budget``, ``apply_id`` and ``source_session`` are legitimately ``None``
    for a caller that is not an apply — an unbudgeted single install files a
    receipt with no apply linkage, which is what that column's nullability
    means.
    """

    bot_id: str
    entity_id: str
    env: str
    tenant: str
    engine_type: str
    actor_id: str
    apply_id: Optional[str]
    budget: Optional[ApplyFetchBudget]
    source_session: Optional[SourceSession]


def scope_of(ctx: FetchContext) -> ContentScope:
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
        content: ManifestContentServiceProtocol,
        credentials: SourceCredentialServiceProtocol,
        objects: ObjectStoreClientFactory,
    ) -> None:
        self._fetcher = fetcher
        self._content = content
        self._credentials = credentials
        # Required, not defaulted. It was ``Optional[...] = None`` so that
        # rigs driving only the git road need not assemble a factory — but
        # the composition root always binds one, so the type said "may be
        # absent" about a value that never is, and bought a ``None`` branch
        # in ``acquire_object`` that production could not reach. Rigs pass
        # ``InMemoryObjectStoreClientFactory()``; it costs them one line and
        # buys everyone an honest signature.
        self._objects = objects
        # Bound once, to this pipeline: the fetchers are strategies over these
        # same collaborators, so every road files receipts under one policy
        # and W11's lineage cannot answer differently by protocol.
        self._fetchers = build_fetchers(self)

    def fetch(
        self,
        ctx: FetchContext,
        *,
        source_url: str,
        digest: Optional[str] = None,
        auth: Optional[str] = None,
        category: str,
        keep_last: bool = False,
        entry_identity: Optional[str] = None,
    ) -> FetchedEntry:
        """Acquire one entry's bytes. Raises :class:`EntryFetchError`.

        ``${BOT_*}`` substitution happens **before** the fetch and therefore
        before prefix authorization: the W3 policy re-authorises every hop
        against the URL the request will actually name, so a substituted URL
        cannot steer the request outside its credential's prefixes (or inside
        them, unseen).

        ``category`` and ``entry_identity`` are REQUIRED keyword-only
        (no defaults): the W11 linkage columns exist so a receipt can name
        the fetch's apply, category and entry — a default here would let a
        future call site silently file unattributed receipts, and the
        linkage's whole point is that there are none. ``entry_identity``
        may still be ``None`` (the fetch pipeline genuinely does not know
        on keep_last reuse of a hand-driven fetch), but a caller must say
        so explicitly rather than by omission.
        """
        expired = ctx.budget.expired() if ctx.budget is not None else None
        if expired is not None:
            # Checked BEFORE the network: a budget-exhausted apply must end
            # in bounded time, because its apply lock is held for its whole
            # duration and the stale-lock reaper is TTL-based — the audit's
            # finding was exactly a legitimate apply outrunning the TTL and
            # the reaper handing a live apply's lock to a second one.
            raise EntryFetchError(expired)

        target = substitute(ctx, source_url)
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
                    source_url=target,
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
                        source_url=target,
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
        if ctx.budget is not None:
            # Network bytes only: a store-hit answers a read, not a fetch,
            # which is why the fast path is free.
            ctx.budget.charge(fetched.size_bytes)
        return FetchedEntry(
            content=fetched.bytes,
            digest=fetched.sha256,
            from_store=False,
            source_url=target,
            # The object's own content type is what the source served; the
            # receipt's is the width-checked column copy of the same. Either
            # may be None, and the caller's archive detection treats that as
            # "the source did not say".
            content_type=fetched.content_type,
        )

    def fetch_declared(
        self,
        ctx: FetchContext,
        *,
        entry: Mapping[str, Any],
        category: str,
        entry_identity: Optional[str] = None,
    ) -> EntryDelivery:
        """Resolve one entry's declared source — inline, or by ``from`` name —
        and acquire it. Raises :class:`EntryFetchError`.

        **The front door does what every road shares, then dispatches.** The
        budget check, the source-session requirement, ``keep_last``, the
        ``from`` lookup and parsing the declaration happen once, here; the
        protocol's own fetcher does the rest. Resolving them centrally is
        what keeps the roads from drifting — two fetchers each deciding what
        ``keep_last`` means would both look correct and disagree.

        One road never reaches a fetcher: a bare-string ``source`` is a URL
        with no declaration to parse, so it goes straight to :meth:`fetch`.
        The schema now **refuses** that spelling at ``PUT`` (§2.2 — declare
        ``protocol: git`` or ``protocol: oss``), so no new document can take
        it. It survives here for the documents already stored under the old
        grammar, which an apply still has to be able to read; delete it only
        once those are known to be gone.

        Every road answers with an :class:`EntryDelivery` — see
        ``apply/entry_delivery.py`` for why the caller does not branch on
        which one it got.
        """
        expired = ctx.budget.expired() if ctx.budget is not None else None
        if expired is not None:
            raise EntryFetchError(expired)

        # The entry's own inline ``source:`` — the alternative to naming a
        # declared one with ``from:``. Three shapes reach here, and the type
        # is the dispatch::
        #
        #     source: {protocol: git, url: ..., ref: v1.2.0}   # Mapping
        #     source: {protocol: oss, bucket: b, key: k}       # Mapping
        #     source: "https://example.com/x.zip"              # str, legacy
        #
        # ``None`` when the entry used ``from:`` or inline ``content:``.
        inline = entry.get("source")
        # Only the roads that read the session require one: a ``from`` name
        # is looked up in ``session.sources`` and a git road checks out
        # through it. The inline-URL road never touches it, and refusing it
        # over a missing session would break the URL-only applies (and
        # their rigs) that W5 shipped — the message says who it is for.
        needs_session = isinstance(entry.get("from"), str) or isinstance(
            inline, Mapping
        )
        session = ctx.source_session
        if needs_session and session is None:
            raise EntryFetchError(
                "this apply carries no source session: a 'from' or git "
                "source needs one (the apply service builds it per apply)"
            )

        keep_last = entry.get("on_fetch_failure", "keep_last") == "keep_last"
        name: Optional[str] = None
        raw: Optional[Mapping[str, Any]] = None

        if isinstance(entry.get("from"), str):
            name = entry["from"]
            raw = session.sources.get(name)
            if raw is None:
                raise EntryFetchError(
                    f"'from' names source {name!r}, which is not declared "
                    "under 'sources'"
                )
        elif isinstance(inline, str):
            return BlobDelivery(
                self.fetch(
                    ctx,
                    source_url=inline,
                    digest=entry.get("digest"),
                    auth=entry.get("auth"),
                    category=category,
                    keep_last=keep_last,
                    entry_identity=entry_identity,
                )
            )
        elif isinstance(inline, Mapping):
            raw = inline
        else:
            raise EntryFetchError(
                "an entry must name one of 'from', 'source' or 'content'"
            )
        assert raw is not None

        # The one place a stored declaration is read. The PUT validator parses
        # through the same pure function, so a document that was accepted
        # cannot fail here on vocabulary — and a document that skipped the
        # validator (W8's hand-built apply points) meets the identical rules
        # rather than a weaker apply-time re-derivation.
        decl, violations = parse_source(raw)
        if decl is None:
            where = f"source {name!r}" if name is not None else "the entry's source"
            raise EntryFetchError(
                f"{where} is not a valid declaration: "
                + "; ".join(v.message for v in violations)
            )

        fetcher = self._fetchers.get(decl.protocol)
        if fetcher is None:
            # Unreachable by construction — ``source_fetchers`` refuses at
            # import to leave a declarable protocol unserved, and ``parse_source``
            # only yields declarable ones. Stated rather than assumed: a
            # ``KeyError`` here would surface as an apply crash, not a refusal.
            raise EntryFetchError(
                f"no fetcher serves protocol {decl.protocol.value!r}"
            )
        return fetcher.fetch(
            DeclaredFetch(
                ctx=ctx,
                decl=decl,
                entry=entry,
                # Coerced once, here, at the only place a ``DeclaredFetch`` is
                # built: an unknown category becomes a loud ``ValueError`` at
                # the front door rather than a quiet fall back to the file cap
                # deep inside a fetcher.
                category=FetchCategory(category),
                entry_identity=entry_identity,
                keep_last=keep_last,
                session=session,
                name=name,
            )
        )

    def _git_keep_last(
        self,
        ctx: FetchContext,
        *,
        session: SourceSession,
        spec: GitSourceSpec,
        display: str,
        keep_last: bool,
    ) -> Optional[FetchedEntry]:
        """`keep_last` for the git road: the receipt of the *last-resolved*
        SHA, when there was one. A first-time source has no baseline — and
        therefore no stored copy entitled to answer for it."""
        if not keep_last:
            return None
        baseline = session.baseline(display)
        if baseline is None:
            return None
        target = git_receipt_url(spec.url, baseline, spec.subpath)
        try:
            receipt = self._content.latest_receipt(
                scope_of(ctx), source_url=target
            )
            if receipt is None:
                return None
            return FetchedEntry(
                content=self._content.read(receipt.digest),
                digest=receipt.digest,
                from_store=True,
                content_type=receipt.content_type,
                source_url=target,
                # The failure is named, never quoted: the git road's transport
                # error text is report-safe on its own ("git fetch failed"),
                # and re-embedding it here would just restate the same words —
                # the reason keep_last fired stays one clean sentence.
                fallback_reason=(
                    "delivered from the platform's stored copy (keep_last): "
                    "the git fetch failed"
                ),
            )
        except (ContentStoreError, ContentStoreFault) as exc:
            raise EntryFetchError(str(exc)) from exc

    def acquire_object(
        self,
        ctx: FetchContext,
        *,
        target: ObjectStoreTarget,
        key: str,
        digest: Optional[str],
        auth: Optional[str],
        category: str,
        keep_last: bool,
        entry_identity: Optional[str],
    ) -> FetchedEntry:
        """One object out of a tenant-named bucket, through the store plugin.

        The same shape :meth:`fetch` has on the URL road — pinned fast path,
        acquire, ``keep_last``, file — with the transport swapped and four of
        the guarded fetcher's six protections gone *because they have nothing
        left to guard*: there is no tenant-supplied URL to shape-check, no
        host to resolve and pin, and no redirect to re-validate, since the
        endpoint comes off the credential row and the client owns the wire.
        The two that survive are the two that were never about the URL: the
        byte cap (enforced inside the client, while streaming) and the
        content address computed here.
        """
        expired = ctx.budget.expired() if ctx.budget is not None else None
        if expired is not None:
            raise EntryFetchError(expired)

        address = object_receipt_url(target.bucket, key)
        scope = scope_of(ctx)
        try:
            receipt = self._content.latest_receipt(scope, source_url=address)
        except (ContentStoreError, ContentStoreFault) as exc:
            raise EntryFetchError(str(exc)) from exc

        if digest is not None and receipt is not None and receipt.digest == digest:
            # Content addressing makes the stored bytes *the* declared bytes,
            # so the store is the strictly more available source of the same
            # truth — the identical ruling the URL road records.
            try:
                return FetchedEntry(
                    content=self._content.read(digest),
                    digest=digest,
                    from_store=True,
                    content_type=receipt.content_type,
                    source_url=address,
                )
            except ContentMissingError:
                logger.warning(
                    "[manifest.object_store] the pinned blob is missing; "
                    "re-reading to heal the platform's copy, digest=%s",
                    digest,
                )
            except (ContentStoreError, ContentStoreFault) as exc:
                raise EntryFetchError(
                    "the platform's copy of the pinned content could not be "
                    f"read: {exc}"
                ) from exc

        limit = FETCH_ENTRY_LIMITS.get(category, FETCH_ENTRY_LIMITS["resources_file"])
        result = self._objects.client_for(target).get(key, byte_limit=limit)

        if result.is_refusal:
            # NOT_FOUND, DENIED, TOO_LARGE — the document or the credential is
            # wrong. keep_last must not mask any of them: a denied credential
            # quietly serving last apply's bytes for a year is exactly the
            # outcome that ruling exists to prevent.
            raise EntryFetchError(result.detail)

        if result.status is not ObjectFetchStatus.FOUND:
            # UNAVAILABLE: the store could not be reached, which is what
            # keep_last is for.
            if keep_last and receipt is not None and (
                digest is None or receipt.digest == digest
            ):
                try:
                    return FetchedEntry(
                        content=self._content.read(receipt.digest),
                        digest=receipt.digest,
                        from_store=True,
                        content_type=receipt.content_type,
                        source_url=address,
                        fallback_reason=(
                            "delivered from the platform's stored copy "
                            f"(keep_last): {result.detail}"
                        ),
                    )
                except (ContentStoreError, ContentStoreFault) as read_exc:
                    raise EntryFetchError(
                        f"{result.detail}; the keep_last fallback copy could "
                        f"not be read: {read_exc}"
                    ) from read_exc
            raise EntryFetchError(result.detail)

        content = result.content or b""
        computed = "sha256:" + hashlib.sha256(content).hexdigest()
        if digest is not None and computed != digest:
            # The pin, checked here rather than in the client: a plugin has no
            # business knowing what a manifest digest is, and this is the one
            # place both roads agree on what "the declared bytes" means.
            raise EntryFetchError(
                f"the object's bytes are {computed}, the entry declared {digest}"
            )

        fetched = FetchedObject(
            bytes=content,
            sha256=computed,
            size_bytes=len(content),
            url=address,
            content_type=None,
            fetched_at=datetime.now(timezone.utc),
        )
        try:
            self._content.store(
                fetched,
                scope=scope,
                source_url=address,
                credential_name=auth,
                modifier=ctx.actor_id,
                apply_id=ctx.apply_id,
                category=category,
                entry_identity=entry_identity,
            )
        except (ContentStoreError, ContentStoreFault) as exc:
            raise EntryFetchError(
                "the fetched bytes could not be filed with the platform's "
                f"store: {exc}"
            ) from exc
        if ctx.budget is not None:
            ctx.budget.charge(len(content))
        return FetchedEntry(
            content=content,
            digest=computed,
            from_store=False,
            source_url=address,
        )

    def file_bytes(
        self,
        ctx: FetchContext,
        *,
        content: bytes,
        source_url: str,
        category: str,
        entry_identity: Optional[str] = None,
        content_type: Optional[str] = None,
        credential_name: Optional[str] = None,
    ) -> str:
        """File entry-level bytes the wire never fetched — the git road's
        canonical form (a package's canonical zip, a single file's bytes)
        — so audit and ``keep_last`` read the same store everyone else does.

        ``credential_name`` threads the acquisition's auth into the W11
        lineage exactly as the URL road's store call does, so a git-sourced
        receipt answers "which named credential distributed this content"
        the same way a URL-sourced one answers it.

        Returns the content digest. Raises :class:`EntryFetchError` on a
        store fault; the charge against the apply budget keeps the ledger
        honest about what the entry cost, disk-read or not.
        """
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        obj = FetchedObject(
            bytes=content, sha256=digest, url=source_url,
            content_type=content_type,
            fetched_at=datetime.now(timezone.utc), size_bytes=len(content),
        )
        try:
            self._content.store(
                obj, scope=scope_of(ctx), source_url=source_url,
                credential_name=credential_name,
                modifier=ctx.actor_id, apply_id=ctx.apply_id,
                category=category, entry_identity=entry_identity,
            )
        except (ContentStoreError, ContentStoreFault) as exc:
            raise EntryFetchError(
                "the bytes could not be filed with the platform's store: "
                f"{exc}"
            ) from exc
        if ctx.budget is not None:
            ctx.budget.charge(len(content))
        return digest

    def _fetch(
        self,
        ctx: FetchContext,
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


def declared_protocol(
    ctx: FetchContext, entry: Mapping[str, Any]
) -> Optional[SourceKind]:
    """Which protocol :meth:`EntryFetcher.fetch_declared` will take, **without
    fetching anything**.

    A caller needs this when a rule has to be decided *before* the network is
    touched — the resources materialiser validates an archive's ``unpack``
    before spending a fetch that a missing one guarantees to waste, and the
    ``cli_tools`` materialiser asks whether the digest rule applies. Both used
    to answer it their own way, or after the fact.

    Read through the same parser the ``PUT`` validator and ``fetch_declared``
    use, so a fourth derivation cannot appear. ``None`` means "cannot say from
    the declaration alone" — an inline URL string is ``OSS`` and a malformed or
    undeclared source is ``None``; the caller then falls through to the fetch,
    which raises the real error with the real message.
    """
    inline = entry.get("source")
    if isinstance(inline, str):
        return SourceKind.OSS
    raw: Any = inline
    if isinstance(entry.get("from"), str):
        session = ctx.source_session
        raw = (getattr(session, "sources", None) or {}).get(entry["from"])
    if not isinstance(raw, Mapping):
        return None
    decl, _ = parse_source(raw)
    return None if decl is None else decl.protocol


__all__ = [
    "declared_protocol",
    "EntryFetchError",
    "EntryFetcher",
    "FetchContext",
    "FetchedEntry",
    "GitEntrySource",
    "scope_of",
]
