"""One entry's fetch, from a declared source to materialisable bytes.

The second half of ``resolve`` for every fetch-consuming category: resolve the
entry's declared source, consult the platform's own copy before the network,
read it through the protocol's own transport under a named credential, and file
the result with the content store so delivery and audit share one copy.

**One entry point**, ``fetch_declared(ctx, *, entry=…)``: the front door
every fetching materialiser calls. It takes no URL at all — it reads the
entry, resolves the declaration and dispatches on its ``protocol``. A receipt
is filed and the budget charged through whichever road it picked.

**What each road does with the source it was handed lives in
``apply/source_fetchers.py``**, and so does the policy it runs under: the two
receipt-identity shapes, pinned vs unpinned, which failures ``keep_last`` may
answer for, and the translation of every store fault into
:class:`EntryFetchError`. This module picks the road; it does not drive one.

Fetch lives here **and only here** — the registry's contract says ``resolve``
is where a category's failures are collected before anything is written, and a
fetch is exactly that kind of failure. Materialisers translate :class:`EntryFetchError`
into their ``ResolveFailure`` currency; nothing about the transport leaks out.
Because the fetch belongs to ``resolve``, a ``dry_run`` may perform one (it
still writes **nothing to the bot** — the store it files with is the
platform's own record of what a bot was served, true whether or not the apply
proceeds).

W7 is the declared-source front door, :meth:`fetch_declared`: the ``from``
and inline-source roads resolve through the apply's source session, and the git
road returns a :class:`GitEntrySource` — the tree is the entry's to
interpret (a file? a package?) — while its canonical, entry-level bytes are
filed with the store by the delivery itself, on the materialiser's
instruction, so audit and ``keep_last`` read the same receipts the object road
files on its way past.

**Dispatch is on the declared protocol**, read through the one parser the
``PUT`` validator also uses. It used to be on which key a source mapping
happened to carry, which meant this module and the validator each derived the
protocol their own way — and the digest rule derived it from a third place
again, against the source *form*, which is how ``from:`` a git source came to
be charged the object-store pin (D5).

A ``source:`` that is a plain string used to be a fourth road — an HTTPS GET
performed here. It is gone: the schema refuses that spelling at ``PUT``, the
management APIs take the content itself, and every source a document can
declare is now an object with a ``protocol``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional


from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    EntryDelivery,
    EntryFetchError,
    FetchedEntry,
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.apply.fetch_context import (
    FetchContext,
    scope_of,
)
from agentclaw.community.core.bot_config_manifest.apply.source_fetchers import (
    DeclaredFetch,
    build_fetchers,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FetchCategory,
)
from agentclaw.community.core.bot_config_manifest.schema.sources import (
    parse_source,
)
from agentclaw.community.core.bot_config_manifest.fetch.object_store import (
    AliyunObjectStore,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind

if TYPE_CHECKING:
    from agentclaw.community.core.bot_config_manifest.content.service_protocol import (
        ManifestContentServiceProtocol,
    )
    from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (
        SourceCredentialServiceProtocol,
    )


class EntryFetcher:
    """Fetches one manifest entry's bytes on a bot's behalf.

    The one funnel every fetching category goes through: ``skills``,
    ``resources``, ``identity`` and ``cli_tools``. One public entry point,
    :meth:`fetch_declared`; it holds no collaborators of its own, only the
    table of roads it dispatches to.

    Composed once per apply — the transport is stateless per hop, so there is
    nothing to hold between entries — and handed to every materialiser that
    fetches. A category that bypassed it would acquire unrecorded bytes, and
    both the audit log and ``keep_last`` read from exactly this log.
    """

    def __init__(
        self,
        content: ManifestContentServiceProtocol,
        credentials: SourceCredentialServiceProtocol,
        objects: AliyunObjectStore,
    ) -> None:
        # Bound once, from this pipeline's three collaborators: the fetchers
        # are strategies over the same store, the same credentials and the
        # same object-store client, so every road files receipts under one
        # policy and W11's lineage cannot answer differently by protocol.
        #
        # ``objects`` is required, not defaulted. It was ``Optional[...] =
        # None`` so that rigs driving only the git road need not assemble a
        # store — but the composition root always binds one, so the type said
        # "may be absent" about a value that never is, and bought a ``None``
        # branch in the object road that production could not reach. Rigs pass
        # ``FakeObjectStore()``; it costs them one line and buys everyone an
        # honest signature.
        self._fetchers = build_fetchers(content, credentials, objects)

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

        ``entry`` is the raw manifest mapping. Which of its keys is present
        decides the road::

            {"path": "data/faq.csv", "from": "content", "subpath": "faq.csv"}
                # a named source: looked up in session.sources, then parsed.
                # Needs a source session.

            {"name": "qc", "source": {"protocol": "oss",
                                      "bucket": "team-artifacts",
                                      "key": "qc/v2.tgz",
                                      "auth": "oss-prod"}}
                # an inline declaration. Needs a source session.

            {"path": "data/kb.zip", "source": "https://example.com/kb.zip"}
                # NOT a road: ``source`` is a declaration object, so this is
                # refused here, the way ``PUT`` refuses it.

        ``category`` is a :class:`FetchCategory` value as a string, e.g.
        ``"resources_file"``; it is coerced to the enum here, so a misspelling
        is a loud ``ValueError`` at the front door rather than a quiet fall
        back to the file cap inside a fetcher.

        Answers an :class:`~...entry_delivery.EntryDelivery` on every road —
        see ``apply/entry_delivery.py`` for why the caller does not branch on
        which one it got.

        **The front door does what every road shares, then dispatches.** The
        budget check, the source-session requirement, ``keep_last``, the
        ``from`` lookup and parsing the declaration happen once, here; the
        protocol's own fetcher does the rest. Resolving them centrally is
        what keeps the roads from drifting — two fetchers each deciding what
        ``keep_last`` means would both look correct and disagree.

        **Every road goes through a fetcher**, because every declarable source
        is an object with a ``protocol``. A ``source:`` that is a plain string
        is not one: it names no protocol, so there is nothing to dispatch on
        and it is refused here with the declaration form it should have taken.
        The schema refuses that spelling at ``PUT`` too, so only a document
        stored under the old grammar can still reach this refusal.
        """
        expired = ctx.budget.expired() if ctx.budget is not None else None
        if expired is not None:
            raise EntryFetchError(expired)

        # The entry's own inline ``source:`` — the alternative to naming a
        # declared one with ``from:``. One shape reaches here, a declaration
        # object, and its ``protocol`` is the dispatch::
        #
        #     source: {protocol: git, url: ..., ref: v1.2.0}
        #     source: {protocol: oss, bucket: b, key: k}
        #
        # ``None`` when the entry used ``from:`` or inline ``content:``.
        inline = entry.get("source")
        # Every road that reaches a fetcher reads the session — a ``from``
        # name is looked up in ``session.sources``, and a declared source is
        # acquired through it — so the requirement is exactly "this entry
        # declares a source". An entry that declares none is refused below on
        # its own terms rather than blamed on the missing session, and the
        # message says who builds one, for the rig that arrives without it.
        declares_source = isinstance(entry.get("from"), str) or isinstance(
            inline, Mapping
        )
        session = ctx.source_session
        if declares_source and session is None:
            raise EntryFetchError(
                "this apply carries no source session: a declared 'from' or "
                "'source' needs one (the apply service builds it per apply)"
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
        elif isinstance(inline, Mapping):
            raw = inline
        else:
            raise EntryFetchError(
                "an entry must name one of 'from', 'source' or 'content', "
                "and 'source' is a declaration object carrying a 'protocol' "
                "(declare 'protocol: git' or 'protocol: oss') — a URL written "
                "as a plain string is not one"
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


def declared_protocol(
    ctx: FetchContext, entry: Mapping[str, Any]
) -> Optional[SourceKind]:
    """Which protocol :meth:`EntryFetcher.fetch_declared` will take, **without
    fetching anything**.

    ==============================================  ====================
    ``entry``                                       Answer
    ==============================================  ====================
    ``{"source": {"protocol": "oss", "bucket":      ``SourceKind.OSS``
    "b", "key": "k", "auth": "a"}}``
    ``{"source": {"protocol": "git", "url":         ``SourceKind.GIT``
    "https://x/y.git"}}``                           
    ``{"from": "content"}`` naming a git source     ``SourceKind.GIT``
    ``{"from": "nope"}``, not under ``sources``     ``None``
    ``{"source": {"protocol": "bogus"}}``           ``None``
    ``{"source": {"protocol": "oss", "bucket":      ``None``
    "b"}}`` — incomplete; ``oss`` requires
    ``auth``, so it does not parse                  
    ``{"source": "https://example.com/kb.zip"}``    ``None``
    ``{"content": "inline text"}``                  ``None``
    ==============================================  ====================

    Anything that is not a declaration answers ``None`` — an incomplete one, a
    bare URL, an absent one — which is exactly what the callers below want:
    ``None`` means "cannot say from the declaration alone", and the fetch is
    then the thing that raises the real error with the real message.

    Called by: ``materialisers/resources``, which validates an archive's
    ``unpack`` before spending a fetch that a missing one guarantees to waste,
    and ``materialisers/cli_tools``, which asks whether the digest rule
    applies.

    Read through the same parser the ``PUT`` validator and ``fetch_declared``
    use, so a fourth derivation cannot appear.
    """
    raw: Any = entry.get("source")
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
