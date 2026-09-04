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
and inline-git roads resolve through the apply's source session, and the git
road returns a :class:`GitEntrySource` — the tree is the entry's to
interpret (a file? a package?) — while its canonical, entry-level bytes are
filed with the store via :meth:`file_bytes`, so audit and ``keep_last`` read
the same receipts the URL roads always have.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional, Protocol, runtime_checkable

import httpx

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
    GitCheckout,
    GitSourceSpec,
    git_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
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
    #: The URL the bytes came by, when the caller needs it for shape
    #: inference (the skills materialiser's archive-kind detection). ``None``
    #: on the roads that never knew one.
    source_url: Optional[str] = None


@dataclass(frozen=True)
class GitEntrySource:
    """A fresh git checkout for one entry to consume — files, not bytes.

    The URL road hands back bytes because there was exactly one blob on the
    wire; the git road hands back a proven tree and lets the materialiser
    read it, because what "the entry's bytes" are (a file? a package? a
    canonical zip?) is a *category* question the fetch layer must not answer.

    ``file_limit`` is the category's per-entry byte cap (the same
    ``FETCH_ENTRY_LIMITS`` number the URL road enforces at the transport) —
    the tree's readers refuse a member by its *declared* size against it.
    ``auth`` is the credential **name** the acquisition rode, threaded to the
    receipts so W11's lineage attributes git-sourced bytes the way it does
    URL-sourced ones.
    """

    checkout: GitCheckout
    source_url: str
    subpath: Optional[str]
    moved_from: Optional[str]
    auth: Optional[str] = None
    file_limit: Optional[int] = None

    def files(self) -> list[tuple[str, bytes]]:
        try:
            return self.checkout.files(self.subpath, file_limit=self.file_limit)
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc

    def read_file(self) -> bytes:
        try:
            return self.checkout.read_file(self.subpath, file_limit=self.file_limit)
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc

    def receipt_url(self) -> str:
        """The W11 identity for this entry's git-sourced bytes."""
        return git_receipt_url(self.source_url, self.checkout.sha, self.subpath)

    def moved_note(self) -> Optional[str]:
        """The non-strict road's report line about a moved ref."""
        if self.moved_from is None:
            return None
        return (
            f"ref moved: the last apply recorded {self.moved_from}, "
            f"this one resolved {self.checkout.sha}"
        )


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
    budget: Optional["ApplyFetchBudget"]
    source_session: Optional[SourceSession]


def scope_of(ctx: "FetchContext") -> ContentScope:
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
        ctx: "FetchContext",
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
        ctx: "FetchContext",
        *,
        entry: "Mapping[str, Any]",
        category: str,
        entry_identity: Optional[str] = None,
    ) -> "FetchedEntry | GitEntrySource":
        """Resolve one entry's declared source — inline, or by ``from`` name —
        and acquire it. Raises :class:`EntryFetchError`.

        The URL roads (inline string ``source``, or a ``from`` source that
        declares ``url``) delegate to :meth:`fetch` unchanged, fold in the
        *source's* ``auth``, and inherit its pinned/keep_last policy. The git
        road resolves the ref once per ``(url, ref)`` per apply through the
        context's source session, enforces ``mode`` against the last apply's
        resolved SHA, and hands back a :class:`GitEntrySource` — the tree is
        the entry's to interpret, and ``keep_last`` falls back to the
        baseline-SHA receipt with the same keep_last-only ruling as wire
        failures.
        """
        expired = ctx.budget.expired() if ctx.budget is not None else None
        if expired is not None:
            raise EntryFetchError(expired)

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
        decl: Optional["Mapping[str, Any]"] = None

        if isinstance(entry.get("from"), str):
            name = entry["from"]
            decl = session.sources.get(name)
            if decl is None:
                raise EntryFetchError(
                    f"'from' names source {name!r}, which is not declared "
                    "under 'sources'"
                )
        elif isinstance(inline, str):
            return self.fetch(
                ctx,
                source_url=inline,
                digest=entry.get("digest"),
                auth=entry.get("auth"),
                category=category,
                keep_last=keep_last,
                entry_identity=entry_identity,
            )
        elif isinstance(inline, Mapping):
            decl = inline
        else:
            raise EntryFetchError(
                "an entry must name one of 'from', 'source' or 'content'"
            )
        assert decl is not None

        if "git" not in decl:
            # A named or inline URL source: the same road, with the source's
            # own auth — the declaration, not the entry, carries it (W7).
            return self.fetch(
                ctx,
                source_url=decl["url"],
                digest=entry.get("digest"),
                auth=decl.get("auth"),
                category=category,
                keep_last=keep_last,
                entry_identity=entry_identity,
            )

        if entry.get("digest") is not None:
            # v1 narrowing, documented: a pin against git-sourced bytes has no
            # stable meaning across the fresh-tree/canonical-zip roads. A
            # SHA-pinned ref is the pin this source speaks.
            raise EntryFetchError(
                "digest pinning is not supported on a git source in v1 — "
                "pin by writing the commit SHA as the source's ref"
            )
        if entry.get("subpath") is not None:
            # The same narrowing, the same style: an entry-level 'subpath' is
            # real vocabulary on the URL roads (it scopes an archive's
            # subtree), and a caller who writes it beside a git source
            # believes they scoped something they did not. One checkout serves
            # *every* entry that names the source, so scoping belongs to the
            # declaration the tree is read by.
            raise EntryFetchError(
                "entry-level 'subpath' is not supported on a git source in "
                "v1 — declare 'subpath' on the source itself, which is what "
                "the tree is read by"
            )
        if entry.get("auth") is not None:
            # Schema already refuses this next to 'from'; an inline git
            # source reaches here with it, and the fetch must not quietly
            # fetch anonymously under a credential the caller believes rode.
            raise EntryFetchError(
                "entry-level 'auth' is not supported on a git source in v1 — "
                "declare 'auth' inside the source object; the credential "
                "applies to the fetch the source names"
            )

        spec = GitSourceSpec(
            url=_substitute(ctx, decl["git"]),
            ref=decl.get("ref") or "HEAD",
            subpath=decl.get("subpath"),
            mode=decl.get("mode") or "non_strict",
        )
        display = name if name is not None else spec.url
        auth = decl.get("auth")

        try:
            headers: dict[str, str] = {}
            if auth:
                binding = self._credentials.binding(name=auth)
                binding.reauthorize(httpx.URL(spec.url))
                headers = dict(binding.headers_for(httpx.URL(spec.url)))
            checkout, fresh = session.checkout(
                spec, headers=headers, display=display
            )
        except CredentialError as exc:
            raise EntryFetchError(str(exc)) from exc
        except PrefixAuthorizationError as exc:
            raise EntryFetchError(str(exc)) from exc
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc
        except FetchFailedError as exc:
            fallback = self._git_keep_last(
                ctx, session=session, spec=spec, display=display,
                keep_last=keep_last,
            )
            if fallback is not None:
                return fallback
            raise EntryFetchError(str(exc)) from exc

        if fresh:
            # The wire really moved for this (url, ref) in THIS apply — the
            # tree's declared bytes are what the ledger that bounds one
            # apply's total download must count. A cached checkout (another
            # entry sharing the source) answers a read, not a fetch, so it is
            # free — the same ruling the URL road's store fast path records.
            if ctx.budget is not None:
                ctx.budget.charge(checkout.tree_bytes)
                expired = ctx.budget.expired()
                if expired is not None:
                    raise EntryFetchError(expired)

        baseline = session.baseline(display)
        if (
            spec.mode == "strict"
            and baseline is not None
            and baseline != checkout.sha
        ):
            raise EntryFetchError(
                f"strict source {display!r} moved: the last apply recorded "
                f"{baseline}, this one resolved {checkout.sha} — the entry "
                "is refused and the bot keeps running what it has"
            )
        # Adopted AFTER the strict gate: a refused move must not write the
        # moved SHA into this apply's report, because the next apply reads
        # its baseline from there — adopting here would turn strict mode
        # into "refuse each move exactly once, then deliver it".
        session.adopt(
            display=display, spec=spec, checkout=checkout, auth_name=auth
        )
        moved = baseline if (baseline is not None and baseline != checkout.sha) else None
        return GitEntrySource(
            checkout=checkout,
            source_url=spec.url,
            subpath=spec.subpath,
            moved_from=moved,
            auth=auth,
            file_limit=FETCH_ENTRY_LIMITS.get(
                category, FETCH_ENTRY_LIMITS["resources_file"]
            ),
        )

    def _git_keep_last(
        self,
        ctx: "FetchContext",
        *,
        session: SourceSession,
        spec: GitSourceSpec,
        display: str,
        keep_last: bool,
    ) -> "Optional[FetchedEntry]":
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

    def file_bytes(
        self,
        ctx: "FetchContext",
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
        ctx: "FetchContext",
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


def _substitute(ctx: "FetchContext", source_url: str) -> str:
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


__all__ = [
    "EntryFetchError",
    "EntryFetcher",
    "FetchContext",
    "FetchedEntry",
    "GitEntrySource",
    "scope_of",
]
