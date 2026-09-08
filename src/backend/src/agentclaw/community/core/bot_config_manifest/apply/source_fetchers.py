"""One fetcher per protocol, and the table that picks between them.

``fetch_declared`` used to end in ``if decl.protocol is not SourceKind.GIT:``
with the object road above it and ninety lines of git below. Adding a protocol
meant editing that branch, and every consumer downstream with it.

Here the front door does the work that is the same on every road — budget,
session, ``keep_last``, and parsing the declaration once — then looks the
protocol up in :data:`FETCHERS` and calls it. Adding a protocol is a class and
a row; the branch has no third arm to grow.

**The table is exhaustive by construction**, the discipline
``support_matrix._build_matrix`` already uses: a :class:`SourceKind` with no
fetcher raises at import rather than ``KeyError``-ing at apply time, in front
of a bot, with the apply lock held.

Each fetcher holds the :class:`~...entry_fetch.EntryFetcher` that owns it —
its transport, its content store, its credentials. They are strategies over
one pipeline's collaborators, not independent pipelines: two fetchers that
each built their own store would file receipts under two policies, and W11's
lineage would answer differently depending on which road served an entry.
"""
from __future__ import annotations

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
    """One entry's acquisition, resolved down to what any protocol needs.

    Assembled once by the front door so no fetcher re-derives it — two
    fetchers each deciding what ``keep_last`` means, or each looking up the
    ``from`` name, is how the roads drift apart while both look correct.
    """

    ctx: "FetchContext"
    decl: SourceDecl
    entry: Mapping[str, Any]
    category: str
    entry_identity: Optional[str]
    #: ``on_fetch_failure`` already resolved to its boolean.
    keep_last: bool
    #: ``None`` only on roads that need no session; a fetcher that reads it
    #: is one the front door already refused without one.
    session: Optional[SourceSession]
    #: The declared ``from`` name, or ``None`` for an inline source. The
    #: report's name for the source, and the key its baseline is read by.
    name: Optional[str]


@runtime_checkable
class SourceFetcher(Protocol):
    """Acquire one entry's content over one protocol."""

    def fetch(self, request: DeclaredFetch) -> EntryDelivery:
        """Raises :class:`EntryFetchError` with a report-safe reason."""
        ...


class ObjectStoreFetcher:
    """``protocol: oss`` — one request, one object, addressed by the source.

    The source carries the credential (W7 — the declaration, not the entry).
    An entry's ``subpath`` is **not** part of the address: on this road it
    selects inside the fetched object, which the materialiser applies after
    unpacking. One rule, both protocols — ``subpath`` selects within what the
    source delivered; git delivers a tree, an object store delivers an object.
    """

    def __init__(self, owner: "EntryFetcher") -> None:
        self._owner = owner

    def fetch(self, request: DeclaredFetch) -> EntryDelivery:
        decl, entry = request.decl, request.entry
        key = compose_key(decl.key, entry.get("key"))
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
            ).object_store_target(decl.bucket or "")
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


class GitSourceFetcher:
    """``protocol: git`` — one checkout per ``(url, ref)`` per apply.

    The ref resolves once through the apply's source session, ``mode`` is
    enforced against the last apply's resolved SHA, and what comes back is a
    tree for the entry to interpret. ``keep_last`` falls back to the
    baseline-SHA receipt under the same keep_last-only ruling wire failures
    get: a *refusal* is configuration and must not be masked, a *failure* is
    the transport and may be.
    """

    def __init__(self, owner: "EntryFetcher") -> None:
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
                file_limit=FETCH_ENTRY_LIMITS.get(
                    request.category, FETCH_ENTRY_LIMITS["resources_file"]
                ),
            )
        )


#: Which fetcher serves which protocol. ``CONTENT`` is absent on purpose: it
#: is a :class:`SourceKind` but never a *source* — inline text is written on
#: the entry and there is nothing to acquire.
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


def build_fetchers(owner: "EntryFetcher") -> Mapping[SourceKind, SourceFetcher]:
    """The table, bound to one pipeline's collaborators."""
    return MappingProxyType(
        {kind: cls(owner) for kind, cls in FETCHER_TYPES.items()}
    )


def object_receipt_url(bucket: str, key: str) -> str:
    """The W11 identity for bytes read out of a bucket.

    ``oss://bucket/key`` — deliberately not a fetchable URL. It is a stable
    *name* for a delivery, the way ``git_receipt_url`` names a tree at a
    commit: the endpoint is not part of it, because the same object read
    through an internal and an external endpoint is the same object, and a
    receipt keyed on the endpoint would file it twice and let ``keep_last``
    miss its own copy.
    """
    return f"oss://{bucket}/{key.lstrip('/')}"


def compose_key(source_key: Optional[str], entry_key: Any) -> Optional[str]:
    """The source's key prefix, then the entry's — one object name.

    Exactly ``compose_subpath``'s rule on the other road, and re-checked by
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

    **This is what lets one source serve many entries.** Before it, an entry
    ``subpath`` beside a git source was refused at apply time (defect D3), so a
    source addressed exactly one file and a second file from the same
    repository needed a second source block carrying duplicate ``url``, ``ref``
    and ``auth`` — reintroducing precisely the drift named sources exist to
    remove. ``resources`` over git is not useful without it: a resources entry
    names a workspace ``path`` *and* a source path, and those differ per entry
    by construction.

    The composed value is re-checked by the schema's own pure predicate rather
    than by a local rule. Two safe segments can compose into an unsafe path
    (``a/b`` under ``..``-free halves is fine, but the join is what the
    checkout is finally asked to read), and a second, weaker rule here is
    exactly how a traversal gets through one layer by satisfying the other.
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


def substitute(ctx: "FetchContext", source_url: str) -> str:
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
    "DeclaredFetch",
    "FETCHER_TYPES",
    "GitSourceFetcher",
    "ObjectStoreFetcher",
    "SourceFetcher",
    "build_fetchers",
    "compose_subpath",
    "substitute",
]
