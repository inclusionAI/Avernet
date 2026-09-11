"""One fetcher per protocol, and the table that picks between them.

The front door (``source_resolver.resolve``) does the work that is the same
on every road — budget, session, ``keep_last``, and parsing the declaration
once — then looks the protocol up in :data:`FETCHER_TYPES` and calls it.
Adding a protocol is a class and a row; the branch has no third arm to grow.

**The table is exhaustive by construction**, the discipline
``support_matrix._build_matrix`` already uses: a :class:`SourceKind` with no
fetcher raises at import rather than ``KeyError``-ing at apply time, in front
of a bot, with the apply lock held.

Each fetcher is handed the collaborators its own road needs — the content
store and W3's credentials, plus the object-store client on the road that has
one. They are strategies over **one apply's** collaborators, not independent
pipelines: :func:`build_fetchers` binds them all from the same three, so two
fetchers cannot each end up with their own store, filing receipts under two
policies while W11's lineage answers differently depending on which road
served an entry.

**Two ``source_url`` shapes exist**, both built here, and both are receipt
identities rather than fetchable URLs — the address bytes are *filed* under,
never an address a fetcher dials::

    "oss://team-artifacts/tools/qc/v2.tgz"
        built by :func:`object_receipt_url` inside
        :meth:`ObjectStoreFetcher._acquire`. The endpoint is deliberately
        absent: it comes off the credential row, not off the document.

    "git+https://code.example.com/team/content.git@<40-hex sha>:kb/faq.csv"
        built by ``fetch/git_source.git_receipt_url``. Keyed on the resolved
        sha, and ends in a bare colon when there is no subpath.

``FetchedEntry.source_url`` and every receipt ``source_url`` is one of the two,
because they are written by whichever road ran.

Pinned vs unpinned is a declared-digest question, and it decides the
store-first vs read-first policy:

* **pinned** (the entry declares a ``digest``) — a receipt for this bot and
  source address *matching the declaration* is the bytes, by content
  addressing: no network is consulted to acquire what the platform already
  holds. A source that has since moved is irrelevant — the declaration asks
  for the pinned bytes, and a re-read of them would only fail the pin.
* **unpinned** — the entry wants whatever is there *now*; every apply
  re-reads so it converges to the source, and ``keep_last`` exists for the
  day that read fails.

``keep_last`` (on a real read failure) reads the latest receipt: bytes that
are entitled to be reused when the declaration pinned nothing, and bytes that
must match the pin when it did — a receipt that disagrees with a declared
digest is not "last", it is stale, and supplying it would silently pin bytes
the declaration never named.

**Which failures may fall back is fixed by what they are a statement about,
and the ruling is:** the source was reachable and could not serve — an
unreachable object store, a git fetch that failed — may fall back; a refusal
may not. A refusal (an object the document names that is not there, a
credential the store will not accept, an object past the category's cap) is a
statement about the document's or the credential's *configuration*, while
keep_last exists for statements about the source's *availability*. Masking a
refusal with stored bytes would answer SUCCEEDED to a document the platform
just refused; the same ruling keeps a deleted credential name loud —
configuration drift is for the author to fix, not for the stored copy to
absorb.

**Every interaction with the content store — lookup, both reads, the
re-file after a read — is translated here** into :class:`EntryFetchError`:
a store-side fault answers as that ENTRY's failure with the store's own
message, never as an unrelated exception escaping resolve to abort the
whole category under a wrapped surprise. The one leniency: a pinned
store-hit whose blob has gone missing falls through to the source —
the pin is byte-provable, so a re-read re-filed with the store heals the
address and nobody upstream learns anything happened.

Grammar reference: ``docs/bot-config-manifest/manifest-schema.zh-CN.md``
— §2.2 (``protocol``), §5 (per-entry limits). Cite a section rather than restating the grammar here;
two copies of one grammar drift, and the document is the one users read.
"""
from __future__ import annotations

import hashlib
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

import httpx

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    BlobDelivery,
    EntryDelivery,
    EntryFetchError,
    FetchedEntry,
    GitDelivery,
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.apply.fetch_context import (
    FetchContext,
    scope_of,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.content.service_protocol import (
    ManifestContentServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.content.errors import (
    ContentMissingError,
    ContentStoreError,
    ContentStoreFault,
)
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
)
from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (
    SourceCredentialServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.credentials.policy import (
    PrefixAuthorizationError,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
    git_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchedObject,
    FetchFailedError,
    FetchRefusedError,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
    FetchCategory,
)
from agentclaw.community.core.bot_config_manifest.fetch.object_store import (
    AliyunObjectStore,
    ObjectFetchStatus,
    ObjectStoreTarget,
)
from agentclaw.community.core.bot_config_manifest.schema import placeholders
from agentclaw.community.core.bot_config_manifest.schema._support import (
    relative_path_refusal,
)
from agentclaw.community.core.bot_config_manifest.schema.sources import SourceDecl
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind
from agentclaw.community.log import get_logger

logger = get_logger()


@dataclass(frozen=True)
class DeclaredFetch:
    """One entry's acquisition request, resolved down to what any protocol needs.

    This is the *argument* every fetcher takes: one manifest entry, plus
    everything the front door already worked out about it, so a fetcher can
    do its protocol and nothing else. Given::

        sources:
          artifacts:
            protocol: oss
            bucket: team-artifacts
            key: tools/
            auth: oss-prod

        manifest:
          cli_tools:
            - name: qc
              from: artifacts
              key: qc/v2.tgz
              on_fetch_failure: keep_last

    (``auth`` is mandatory on an ``oss`` source: the endpoint and the key pair
    come off the named credential, so a declaration without one does not parse.)

    the front door hands :class:`ObjectStoreFetcher`::

        DeclaredFetch(
            ctx=<the ApplyContext>,
            decl=SourceDecl(protocol=SourceKind.OSS, bucket="team-artifacts",
                            key="tools/", auth="oss-prod"),
            entry={"name": "qc", "from": "artifacts", "key": "qc/v2.tgz",
                   "on_fetch_failure": "keep_last"},
            category=FetchCategory.CLI_TOOLS,
            entry_identity="qc",
            keep_last=True,
            session=<the SourceSession>,
            name="artifacts",
        )

    The git road differs only in ``decl`` and in the entry's own keys. For::

        sources:
          content:
            protocol: git
            url: https://code.example.com/team/content.git
            ref: v1.2.0
            subpath: kb
            auth: git-prod

        manifest:
          resources:
            - path: data/faq.csv
              from: content
              subpath: faq.csv

    :class:`GitSourceFetcher` is handed::

        DeclaredFetch(
            ctx=<the ApplyContext>,
            decl=SourceDecl(protocol=SourceKind.GIT,
                            url="https://code.example.com/team/content.git",
                            ref="v1.2.0", subpath="kb", auth="git-prod",
                            mode="non_strict"),
            entry={"path": "data/faq.csv", "from": "content",
                   "subpath": "faq.csv"},
            category=FetchCategory.RESOURCES_FILE,
            entry_identity="data/faq.csv",
            keep_last=True,          # the default when unspecified
            session=<the SourceSession>,
            name="content",
        )

    An **inline** source differs in exactly one field: ``name`` is ``None`` and
    ``decl`` is parsed from the entry's own ``source:`` mapping.

    Created by: ``apply/source_resolver.DeclaredSourceResolver.resolve``, the only
    place one is built.
    Consumed by: :meth:`SourceFetcher.fetch` — that is, both fetchers below.

    Assembled once, by the front door, so no fetcher re-derives it — two
    fetchers each deciding what ``keep_last`` means, or each looking up the
    ``from`` name, is how the roads drift apart while both look correct.
    """

    #: This apply's context. Read for ``env``/``tenant``/``engine_type``
    #: (placeholder substitution) and for ``budget``.
    ctx: FetchContext
    #: The source declaration, already parsed by ``schema.sources.parse_source``
    #: — whether it came from the ``sources`` map or from the entry's own
    #: inline ``source:`` mapping.
    decl: SourceDecl
    #: The manifest entry itself, raw as parsed from YAML. The fetcher reads
    #: only the keys its protocol owns: ``key``/``digest`` on the object road,
    #: ``subpath``/``digest``/``auth`` on the git road.
    entry: Mapping[str, Any]
    #: The fetch category, as the closed vocabulary rather than a bare string,
    #: e.g. ``FetchCategory.CLI_TOOLS``. Typed rather than loose so
    #: ``FETCH_ENTRY_LIMITS`` is a total lookup instead of one guarded by a
    #: default that would silently narrow a mis-typed category to the file cap.
    category: FetchCategory
    #: How the entry names itself in the report, e.g. ``"qc"`` or
    #: ``"data/faq.csv"``. Rides into the receipt as the linkage's entry half.
    #: ``None`` when the caller genuinely does not know.
    entry_identity: Optional[str]
    #: ``on_fetch_failure`` already resolved to its boolean: ``True`` for
    #: ``keep_last`` (the default when the entry says nothing), ``False``
    #: otherwise.
    keep_last: bool
    #: This apply's source session. ``None`` only on roads that need no
    #: session; a fetcher that reads it is one the front door already refused
    #: without one, so :class:`GitSourceFetcher` asserts rather than branches.
    session: Optional[SourceSession]
    #: The declared ``from`` name, e.g. ``"content"``, or ``None`` for an
    #: inline source. The git road falls back to ``<url>@<ref>`` when this is
    #: ``None``, and the result is the ``display`` that names the source in the
    #: report — one row per declaration. The baseline is not read by it: that
    #: is keyed on the substituted ``(url, ref)``.
    name: Optional[str]


@runtime_checkable
class SourceFetcher(Protocol):
    """Acquire one entry's content over one protocol.

    One method, one argument, one return type: :class:`DeclaredFetch` in,
    :class:`~...entry_delivery.EntryDelivery` out. Two implementations ship,
    below.
    """

    @abstractmethod
    def fetch(self, request: DeclaredFetch) -> EntryDelivery:
        """The delivery, or :class:`EntryFetchError` with a report-safe reason.

        The reason lands verbatim on the entry's report row, so it may name a
        credential but never carry its value.
        """
        ...


class ObjectStoreFetcher(SourceFetcher):
    """``protocol: oss`` — one request, one object, addressed by the source.

    Always answers a :class:`~...entry_delivery.BlobDelivery`. The address is
    ``bucket`` from the declaration plus the composed ``key``; the endpoint and
    the key pair come off the named credential, so there is no anonymous road.

    The source carries the credential — the declaration, not the entry. An
    entry's ``subpath`` is **not** part of the address: on this road it selects
    inside the fetched object, which the materialiser applies after unpacking.
    One rule, both protocols — ``subpath`` selects within what the source
    delivered; git delivers a tree, an object store delivers an object.
    """

    def __init__(
        self,
        content: ManifestContentServiceProtocol,
        credentials: SourceCredentialServiceProtocol,
        objects: AliyunObjectStore,
    ) -> None:
        self._content = content
        self._credentials = credentials
        self._objects = objects

    def fetch(self, request: DeclaredFetch) -> EntryDelivery:
        decl, entry = request.decl, request.entry
        # ``${BOT_*}`` resolves in the address the same way it did in a source
        # URL — a per-env bucket and a per-env key prefix are the whole reason
        # the placeholders exist, and dropping substitution here would have
        # made them silently inert on the road that replaced that URL.
        key = compose_key(decl.key, entry.get("key"))
        if key is not None:
            key = substitute(request.ctx, key)
        if not key:
            raise EntryFetchError(
                "an object store entry must name the object: declare 'key' on "
                "the entry, on the source, or both — the source's is a prefix"
            )
        if not decl.auth:
            # No anonymous road. A bucket read needs an endpoint, and the
            # endpoint is a property of the credential — without one there is
            # nowhere to send the request, which is a better failure than
            # inventing a default host.
            raise EntryFetchError(
                "an object store source must declare 'auth': the endpoint and "
                "the key pair come from the named credential"
            )
        try:
            target = self._credentials.binding(
                name=decl.auth
            ).object_store_target(substitute(request.ctx, decl.bucket or ""))
        except CredentialError as exc:
            raise EntryFetchError(str(exc)) from exc
        return BlobDelivery(
            self._acquire(
                request.ctx,
                target=target,
                key=key,
                digest=entry.get("digest"),
                auth=decl.auth,
                category=request.category,
                keep_last=request.keep_last,
                entry_identity=request.entry_identity,
            )
        )

    def _acquire(
        self,
        ctx: FetchContext,
        *,
        target: ObjectStoreTarget,
        key: str,
        digest: Optional[str],
        auth: Optional[str],
        category: FetchCategory,
        keep_last: bool,
        entry_identity: Optional[str],
    ) -> FetchedEntry:
        """One object out of a tenant-named bucket, through the object store.

        Takes the resolved ``ObjectStoreTarget`` (bucket plus the endpoint and
        key pair off the credential row) and the already-composed, already
        substituted ``key``. The address it files under is built here::

            _acquire(ctx,
                     target=ObjectStoreTarget(bucket="team-artifacts",
                                              ...),
                     key="tools/qc/v2.tgz",
                     digest=None,
                     auth="oss-prod",
                     category=FetchCategory.CLI_TOOLS,
                     keep_last=True,
                     entry_identity="qc")
            # -> FetchedEntry(...,
            #        source_url="oss://team-artifacts/tools/qc/v2.tgz")

        ``content_type`` is always ``None`` on this road: the store client
        reports none.

        The shape the module docstring's policy describes — pinned fast path,
        acquire, ``keep_last``, file — over the object store's transport, where
        four of the guarded fetcher's six protections are gone *because they
        have nothing left to guard*: there is no tenant-supplied URL to
        shape-check, no host to resolve and pin, and no redirect to
        re-validate, since the endpoint comes off the credential row and the
        store client owns the wire. The two that survive are the two that were
        never about a URL: the byte cap (enforced inside the client, while
        streaming) and the content address computed here.
        """
        expired = ctx.budget.expired() if ctx.budget is not None else None
        if expired is not None:
            raise EntryFetchError(expired)

        address = object_receipt_url(target.bucket, key)
        scope = scope_of(ctx)
        try:
            receipt = self._content.latest_receipt(
                scope, source_url=address
            )
        except (ContentStoreError, ContentStoreFault) as exc:
            raise EntryFetchError(str(exc)) from exc

        if digest is not None and receipt is not None and receipt.digest == digest:
            # Content addressing makes the stored bytes *the* declared bytes,
            # so the store is the strictly more available source of the same
            # truth — the ruling the module docstring records.
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

        # Total, not ``.get`` with a fallback: ``category`` is the
        # ``FetchCategory`` the front door already coerced, and
        # ``FETCH_ENTRY_LIMITS`` is bound to that enum by the limits module's
        # own assertion — so every member has a cap and a missing one is a bug
        # to raise on rather than to paper over with the file cap. The git
        # road's ``file_limit=`` line reads the table the same way.
        limit = FETCH_ENTRY_LIMITS[category]
        result = self._objects.get(target, key, byte_limit=limit)

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
            # The pin, checked here rather than in the client: a transport has
            # no business knowing what a manifest digest is, and this is the
            # one place both roads agree on what "the declared bytes" means.
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


class GitSourceFetcher(SourceFetcher):
    """``protocol: git`` — one checkout per ``(url, ref)`` per apply.

    Answers a :class:`~...entry_delivery.GitDelivery` on success, and a
    :class:`~...entry_delivery.BlobDelivery` when the fetch failed and
    ``keep_last`` stood in with the baseline SHA's stored tree.

    Refuses two entry keys outright, both with report-safe reasons: ``digest``
    (pin by writing the commit SHA as the source's ref instead) and
    entry-level ``auth`` (declare it inside the source object).

    The ref resolves once through the apply's source session, ``mode`` is
    enforced against the last apply's resolved SHA for the same ``(url, ref)``,
    and what comes back is a tree for the entry to interpret. ``keep_last``
    falls back under the same ruling wire failures get: a *refusal* is
    configuration and must not be masked, a *failure* is the transport and may
    be.
    """

    def __init__(
        self,
        content: ManifestContentServiceProtocol,
        credentials: SourceCredentialServiceProtocol,
    ) -> None:
        self._content = content
        self._credentials = credentials

    def fetch(self, request: DeclaredFetch) -> EntryDelivery:
        ctx, decl, entry = request.ctx, request.decl, request.entry
        session = request.session
        assert session is not None  # the front door refuses a git road without one

        if entry.get("digest") is not None:
            # v1 narrowing, documented: a pin against git-sourced bytes has no
            # stable meaning across the fresh-tree/canonical-zip roads. A
            # SHA-pinned ref is the pin this source speaks.
            raise EntryFetchError(
                "digest pinning is not supported on a git source in v1 — "
                "pin by writing the commit SHA as the source's ref"
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
            url=substitute(ctx, decl.url),
            ref=decl.ref or "HEAD",
            subpath=compose_subpath(decl.subpath, entry.get("subpath")),
            mode=decl.mode,
        )
        # One report row per declaration. An inline source has no name to
        # report under, and the URL alone is not one: two entries reading one
        # repository at two refs would collapse into a single row naming
        # neither ref. ``spec.ref`` is already normalised ("HEAD" when the
        # declaration omitted it), so the display is stable across applies.
        display = (
            request.name if request.name is not None else f"{spec.url}@{spec.ref}"
        )
        auth = decl.auth

        try:
            headers: dict[str, str] = {}
            if auth:
                binding = self._credentials.binding(name=auth)
                binding.reauthorize(httpx.URL(spec.url))
                headers = dict(binding.headers_for(httpx.URL(spec.url)))
            checkout, fresh = session.checkout(spec, headers=headers)
        except CredentialError as exc:
            raise EntryFetchError(str(exc)) from exc
        except PrefixAuthorizationError as exc:
            raise EntryFetchError(str(exc)) from exc
        except FetchRefusedError as exc:
            raise EntryFetchError(str(exc)) from exc
        except FetchFailedError as exc:
            fallback = self._keep_last(
                ctx,
                session=session,
                spec=spec,
                keep_last=request.keep_last,
            )
            if fallback is not None:
                return BlobDelivery(fallback)
            raise EntryFetchError(str(exc)) from exc

        if fresh:
            # The wire really moved for this (url, ref) in THIS apply — the
            # tree's declared bytes are what the ledger that bounds one
            # apply's total download must count. A cached checkout (another
            # entry sharing the source) answers a read, not a fetch, so it is
            # free — the same ruling the object road's store fast path records.
            if ctx.budget is not None:
                ctx.budget.charge(checkout.tree_bytes)
                expired = ctx.budget.expired()
                if expired is not None:
                    raise EntryFetchError(expired)

        baseline = session.baseline(spec.url, spec.ref)
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
        # into "refuse each move exactly once, then deliver it". The baseline
        # is the one recorded for this (url, ref), so editing either in the
        # document asks about a pair nothing has an opinion on yet: a
        # deliberate re-pin passes, and only a pair that resolved differently
        # under its own name is a move.
        session.adopt(
            display=display, spec=spec, checkout=checkout, auth_name=auth
        )
        moved = (
            baseline
            if (baseline is not None and baseline != checkout.sha)
            else None
        )
        return GitDelivery(
            GitEntrySource(
                checkout=checkout,
                source_url=spec.url,
                subpath=spec.subpath,
                moved_from=moved,
                auth=auth,
                # Total, not ``.get`` with a fallback: ``FetchCategory`` and
                # ``FETCH_ENTRY_LIMITS`` are bound by that module's own
                # assertion, so every member has a cap and a missing one is a
                # bug to raise on rather than to paper over with the file cap.
                file_limit=FETCH_ENTRY_LIMITS[request.category],
            ),
            # The one road that still owes the store a write: a checkout is
            # not bytes, so what this entry delivers is the caller's to decide
            # and the delivery's to file. Handed the same store this fetcher
            # reads ``keep_last`` receipts from, so one entry's read and its
            # write cannot end up on two policies.
            self._content,
        )

    def _keep_last(
        self,
        ctx: FetchContext,
        *,
        session: SourceSession,
        spec: GitSourceSpec,
        keep_last: bool,
    ) -> Optional[FetchedEntry]:
        """``keep_last`` for the git road: the receipt of the *last-resolved*
        SHA, when there was one.

        Looks the baseline up by ``(url, ref)`` and reads the receipt filed
        under ``git+<url>@<baseline sha>:<subpath>``. Answers ``None`` — meaning
        "no fallback, let the failure stand" — in three cases: ``keep_last`` is
        off, the pair has no baseline (a first-time pair has no stored copy
        entitled to answer for it, and a freshly re-pinned ``ref`` is a
        first-time pair), or no receipt exists at that address.

        On a hit the :class:`FetchedEntry` carries ``from_store=True`` and a
        ``fallback_reason``, which is what the report's note comes from.
        """
        if not keep_last:
            return None
        baseline = session.baseline(spec.url, spec.ref)
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


#: Which fetcher **class** serves which protocol — the types, not instances::
#:
#:     {
#:         SourceKind.OSS: ObjectStoreFetcher,
#:         SourceKind.GIT: GitSourceFetcher,
#:     }
#:
#: :func:`build_fetchers` is what turns it into instances bound to one
#: pipeline. ``CONTENT`` is absent on purpose: it is a :class:`SourceKind` but
#: never a *source* — inline text is written on the entry and there is nothing
#: to acquire. The import-time check below refuses any other omission.
FETCHER_TYPES: Mapping[SourceKind, type] = MappingProxyType(
    {
        SourceKind.OSS: ObjectStoreFetcher,
        SourceKind.GIT: GitSourceFetcher,
    }
)

_UNSERVED = (set(SourceKind) - {SourceKind.CONTENT}) - set(FETCHER_TYPES)
if _UNSERVED:
    # At import, never at apply. A protocol the schema will happily accept and
    # no fetcher can serve is "the surface accepts what it cannot apply" —
    # the one rule this whole feature is built around — and discovering it
    # from a KeyError mid-apply, with the bot's lock held, is the worst
    # possible time.
    raise RuntimeError(
        "every declarable source protocol needs a fetcher; missing: "
        + ", ".join(sorted(k.value for k in _UNSERVED))
    )


def build_fetchers(
    content: ManifestContentServiceProtocol,
    credentials: SourceCredentialServiceProtocol,
    objects: AliyunObjectStore,
) -> Mapping[SourceKind, SourceFetcher]:
    """The table above, bound to one apply's collaborators::

        {
            SourceKind.OSS: ObjectStoreFetcher(content, credentials, objects),
            SourceKind.GIT: GitSourceFetcher(content, credentials),
        }

    One row per :data:`FETCHER_TYPES` key, spelled out rather than built by
    comprehension, because the two roads do not take the same collaborators:
    only the object road reaches a store client. The table above is still the
    record of *which* protocols are served, and still what the import-time
    check holds exhaustive; this binds them.

    Called once per :class:`~...source_resolver.DeclaredSourceResolver`, in its constructor.
    """
    return MappingProxyType(
        {
            SourceKind.OSS: ObjectStoreFetcher(content, credentials, objects),
            SourceKind.GIT: GitSourceFetcher(content, credentials),
        }
    )


def object_receipt_url(bucket: str, key: str) -> str:
    """The receipt identity for bytes read out of a bucket::

        object_receipt_url("team-artifacts", "tools/qc/v2.tgz")
        # -> "oss://team-artifacts/tools/qc/v2.tgz"

        object_receipt_url("team-artifacts", "/leading/slash.tgz")
        # -> "oss://team-artifacts/leading/slash.tgz"

    **The endpoint is deliberately not part of it**, and this is not an
    omission: the same object read through an internal and an external endpoint
    is the same object, so a receipt keyed on the endpoint would file it twice
    and let ``keep_last`` miss its own copy. The result is therefore a stable
    *name*, not a fetchable URL — the way ``git_receipt_url`` names a tree at a
    commit.
    """
    return f"oss://{bucket}/{key.lstrip('/')}"


def compose_key(source_key: Optional[str], entry_key: Any) -> Optional[str]:
    """The source's key prefix, then the entry's — one object name.

    ====================  ==============  ====================================
    ``source_key``        ``entry_key``   Result
    ====================  ==============  ====================================
    ``"tools/"``          ``"qc/v2.tgz"`` ``"tools/qc/v2.tgz"``
    ``None``              ``"qc/v2.tgz"`` ``"qc/v2.tgz"``
    ``"tools/"``          ``None``        ``"tools/"`` (the prefix is the
                                          whole name)
    ``None``              ``None``        ``None`` (the caller then refuses:
                                          the entry named no object)
    ``"tools"``           ``"/qc.tgz"``   ``"tools/qc.tgz"`` (one slash; the
                                          join strips both sides)
    ====================  ==============  ====================================

    ``entry_key`` is typed ``Any`` because it comes raw from YAML: a non-string
    or an empty string raises :class:`EntryFetchError`, as does a composition
    that fails the schema's path predicate.

    Exactly :func:`compose_subpath`'s rule on the other road, and re-checked by
    the same pure predicate for the same reason: two safe halves can compose
    into an unsafe whole, and a second weaker rule here is how a traversal
    gets through one layer by satisfying the other.
    """
    if entry_key is None:
        return source_key
    if not isinstance(entry_key, str) or not entry_key:
        raise EntryFetchError("entry 'key' must be a non-empty string")
    joined = (
        entry_key
        if not source_key
        else source_key.rstrip("/") + "/" + entry_key.lstrip("/")
    )
    refusal = relative_path_refusal(joined, what="key")
    if refusal is not None:
        raise EntryFetchError(
            f"the source's key and the entry's compose to {joined!r}, "
            f"which is refused: {refusal[1]}"
        )
    return joined


def compose_subpath(
    source_subpath: Optional[str], entry_subpath: Any
) -> Optional[str]:
    """The source's ``subpath``, then the entry's — one path, re-checked.

    ====================  ================  ==================================
    ``source_subpath``    ``entry_subpath`` Result
    ====================  ================  ==================================
    ``"kb"``              ``"faq.csv"``     ``"kb/faq.csv"``
    ``None``              ``"faq.csv"``     ``"faq.csv"``
    ``"kb"``              ``None``          ``"kb"``
    ``None``              ``None``          ``None`` (the whole tree)
    ``"kb"``              ``"../etc"``      raises: "...compose to
                                            'kb/../etc', which is refused:
                                            subpath must not contain a '..'
                                            segment"
    ====================  ================  ==================================

    ``entry_subpath`` is typed ``Any`` because it comes raw from YAML: a
    non-string or an empty string raises :class:`EntryFetchError`.

    **This is what lets one source serve many entries**: ``resources`` over git
    is not useful without it, because a resources entry names a workspace
    ``path`` *and* a source path, and those differ per entry by construction.
    Without composition each file needs its own source block carrying duplicate
    ``url``, ``ref`` and ``auth``.

    The composed value is re-checked by the schema's own pure predicate rather
    than by a local rule. Two safe segments can compose into an unsafe path,
    and a second, weaker rule here is exactly how a traversal gets through one
    layer by satisfying the other.
    """
    if entry_subpath is None:
        return source_subpath
    if not isinstance(entry_subpath, str) or not entry_subpath:
        raise EntryFetchError("entry 'subpath' must be a non-empty string")
    joined = (
        entry_subpath
        if not source_subpath
        else source_subpath.rstrip("/") + "/" + entry_subpath.lstrip("/")
    )
    refusal = relative_path_refusal(joined, what="subpath")
    if refusal is not None:
        raise EntryFetchError(
            f"the source's subpath and the entry's compose to {joined!r}, "
            f"which is refused: {refusal[1]}"
        )
    return joined


def substitute(ctx: FetchContext, source_url: str) -> str:
    """``${BOT_*}`` in one string, against this apply's deployment context.

    Despite the parameter name it is used on every addressing string, not only
    URLs — the object road substitutes the bucket and the composed key too::

        substitute(ctx, "https://cdn.example.com/${BOT_ENV}/kb.zip")
        # -> "https://cdn.example.com/prod/kb.zip"

        substitute(ctx, "${BOT_TENANT}-artifacts")     # a bucket
        # -> "acme-artifacts"

    The three axes come off the context: ``engine_type``, ``env``, ``tenant``.

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
    "DeclaredFetch",
    "FETCHER_TYPES",
    "GitSourceFetcher",
    "ObjectStoreFetcher",
    "SourceFetcher",
    "build_fetchers",
    "compose_subpath",
    "substitute",
]
