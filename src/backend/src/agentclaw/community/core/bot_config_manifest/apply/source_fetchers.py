"""One fetcher per protocol, and the table that picks between them.

The front door (``entry_fetch.fetch_declared``) does the work that is the same
on every road — budget, session, ``keep_last``, and parsing the declaration
once — then looks the protocol up in :data:`FETCHER_TYPES` and calls it.
Adding a protocol is a class and a row; the branch has no third arm to grow.

**The table is exhaustive by construction**, the discipline
``support_matrix._build_matrix`` already uses: a :class:`SourceKind` with no
fetcher raises at import rather than ``KeyError``-ing at apply time, in front
of a bot, with the apply lock held.

Each fetcher holds the :class:`~...entry_fetch.EntryFetcher` that owns it —
its transport, its content store, its credentials. They are strategies over
one pipeline's collaborators, not independent pipelines: two fetchers that
each built their own store would file receipts under two policies, and W11's
lineage would answer differently depending on which road served an entry.

Grammar reference: ``docs/bot-config-manifest/manifest-schema.zh-CN.md``
— §2.2 (``protocol``), §5 (per-entry limits). Cite a section rather than restating the grammar here;
two copies of one grammar drift, and the document is the one users read.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Optional, Protocol, runtime_checkable

import httpx

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    BlobDelivery,
    EntryDelivery,
    EntryFetchError,
    GitDelivery,
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
)
from agentclaw.community.core.bot_config_manifest.credentials.policy import (
    PrefixAuthorizationError,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
    FetchRefusedError,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
    FetchCategory,
)
from agentclaw.community.core.bot_config_manifest.schema import placeholders
from agentclaw.community.core.bot_config_manifest.schema._support import (
    relative_path_refusal,
)
from agentclaw.community.core.bot_config_manifest.schema.sources import SourceDecl
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
        EntryFetcher,
        FetchContext,
    )


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

    Created by: ``apply/entry_fetch.EntryFetcher.fetch_declared``, the only
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
    #: inline source. The git road falls back to the repository URL when this
    #: is ``None``, and the result is the ``display`` that names the source in
    #: the report and keys its baseline.
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

    def __init__(self, owner: EntryFetcher) -> None:
        self._owner = owner

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
            target = self._owner._credentials.binding(
                name=decl.auth
            ).object_store_target(substitute(request.ctx, decl.bucket or ""))
        except CredentialError as exc:
            raise EntryFetchError(str(exc)) from exc
        return BlobDelivery(
            self._owner.acquire_object(
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


class GitSourceFetcher(SourceFetcher):
    """``protocol: git`` — one checkout per ``(url, ref)`` per apply.

    Answers a :class:`~...entry_delivery.GitDelivery` on success, and a
    :class:`~...entry_delivery.BlobDelivery` when the fetch failed and
    ``keep_last`` stood in with the baseline SHA's stored tree.

    Refuses two entry keys outright, both with report-safe reasons: ``digest``
    (pin by writing the commit SHA as the source's ref instead) and
    entry-level ``auth`` (declare it inside the source object).

    The ref resolves once through the apply's source session, ``mode`` is
    enforced against the last apply's resolved SHA, and what comes back is a
    tree for the entry to interpret. ``keep_last`` falls back under the same
    ruling wire failures get: a *refusal* is configuration and must not be
    masked, a *failure* is the transport and may be.
    """

    def __init__(self, owner: EntryFetcher) -> None:
        self._owner = owner

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
        display = request.name if request.name is not None else spec.url
        auth = decl.auth

        try:
            headers: dict[str, str] = {}
            if auth:
                binding = self._owner._credentials.binding(name=auth)
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
            fallback = self._owner._git_keep_last(
                ctx,
                session=session,
                spec=spec,
                display=display,
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
            )
        )


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


def build_fetchers(owner: EntryFetcher) -> Mapping[SourceKind, SourceFetcher]:
    """The table, bound to one pipeline's collaborators::

        {
            SourceKind.OSS: ObjectStoreFetcher(owner),
            SourceKind.GIT: GitSourceFetcher(owner),
        }

    Called once per :class:`~...entry_fetch.EntryFetcher`, in its constructor.
    """
    return MappingProxyType(
        {kind: cls(owner) for kind, cls in FETCHER_TYPES.items()}
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
