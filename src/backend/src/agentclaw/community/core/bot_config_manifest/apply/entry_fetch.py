"""One entry's fetch, from a declared source to materialisable bytes.

The second half of ``resolve`` for every fetch-consuming category: resolve the
entry's declared source, consult the platform's own copy before the network,
read it through the protocol's own transport under a named credential, and file
the result with the content store so delivery and audit share one copy.

**The three entry points**, and which is which:

``fetch_declared(ctx, *, entry=…)``
    The front door every fetching materialiser calls. Takes no URL at all: it
    reads the entry, resolves the declaration and dispatches on its
    ``protocol``. Files a receipt and charges the budget through whichever road
    it picked.

``acquire_object(ctx, *, target=…, key=…)``
    Called by ``source_fetchers.ObjectStoreFetcher``. Takes an
    ``ObjectStoreTarget`` and a key rather than a URL, and builds the
    ``oss://`` receipt identity itself. Files a receipt. Charges the budget on
    a real read.

``file_bytes(ctx, *, content=…, source_url=…)``
    Called by the ``resources``, ``skills`` and ``cli_tools`` materialisers for
    the git road's canonical bytes. Its ``source_url`` is the caller's own
    receipt identity, normally a ``git+…`` one. Files a receipt and always
    charges, because the caller is declaring what the entry cost.

**Two ``source_url`` shapes exist**, and both are receipt identities rather
than fetchable URLs — the address bytes are *filed* under, never an address
this module dials::

    "oss://team-artifacts/tools/qc/v2.tgz"
        built by ``source_fetchers.object_receipt_url`` inside
        ``acquire_object``. The endpoint is deliberately absent: it comes off
        the credential row, not off the document.

    "git+https://code.example.com/team/content.git@<40-hex sha>:kb/faq.csv"
        built by ``fetch/git_source.git_receipt_url``. Keyed on the resolved
        sha, and ends in a bare colon when there is no subpath.

``FetchedEntry.source_url`` and every receipt ``source_url`` is one of the two,
because they are written by whichever road ran.

Fetch lives here **and only here** — the registry's contract says ``resolve``
is where a category's failures are collected before anything is written, and a
fetch is exactly that kind of failure. Materialisers translate :class:`EntryFetchError`
into their ``ResolveFailure`` currency; nothing about the transport leaks out.
Because the fetch belongs to ``resolve``, a ``dry_run`` may perform one (it
still writes **nothing to the bot** — the store it files with is the
platform's own record of what a bot was served, true whether or not the apply
proceeds).

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

W7 is the declared-source front door, :meth:`fetch_declared`: the ``from``
and inline-source roads resolve through the apply's source session, and the git
road returns a :class:`GitEntrySource` — the tree is the entry's to
interpret (a file? a package?) — while its canonical, entry-level bytes are
filed with the store via :meth:`file_bytes`, so audit and ``keep_last`` read
the same receipts the object road files on its way past.

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

import hashlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping, Optional, Protocol, runtime_checkable


from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    EntryDelivery,
    EntryFetchError,
    FetchedEntry,
    GitEntrySource,
)
from agentclaw.community.core.bot_config_manifest.apply.source_fetchers import (
    DeclaredFetch,
    build_fetchers,
    object_receipt_url,
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
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
    git_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
    FetchCategory,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchedObject,
)
from agentclaw.community.core.bot_config_manifest.schema.sources import (
    parse_source,
)
from agentclaw.community.core.bot_config_manifest.fetch.object_store import (
    AliyunObjectStore,
    ObjectFetchStatus,
    ObjectStoreTarget,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind
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

    Nine attributes, no methods. ``ApplyContext`` satisfies it structurally and
    is the usual one; the ``cli_tools`` service passes its own object, because
    it is called by an HTTP route as well as by a materialiser and both must
    fetch through *this* funnel.

    A minimal satisfying value::

        ctx.bot_id        == "bot_42"
        ctx.entity_id     == "ent_7"
        ctx.env           == "prod"
        ctx.tenant        == "acme"
        ctx.engine_type   == "claude_code"
        ctx.actor_id      == "usr_collaborator"
        ctx.apply_id      == "ap_01HZX8"   # or None
        ctx.budget        == ApplyFetchBudget(...)   # or None
        ctx.source_session == SourceSession(...)     # or None

    The first six are read for the store scope, placeholder substitution and
    the receipt's ``modifier``. The last three are legitimately ``None`` for a
    caller that is not an apply: an unbudgeted single install files a receipt
    with no apply linkage, which is what that column's nullability means.

    Declaring the seam rather than annotating ``"ApplyContext"`` while a second
    type is passed keeps the dependency honest: a maintainer who adds a
    ``ctx.something`` read below adds it here too, and the other caller fails
    to type-check instead of failing at apply time.
    """

    bot_id: str
    #: Storage key for the bot; one axis of the content store's scope.
    entity_id: str
    env: str
    tenant: str
    engine_type: str
    #: Who is fetching. Lands on the receipt as ``modifier``.
    actor_id: str
    #: Stamped into every receipt this fetch files. ``None`` off the apply path.
    apply_id: Optional[str]
    budget: Optional[ApplyFetchBudget]
    source_session: Optional[SourceSession]


def scope_of(ctx: FetchContext) -> ContentScope:
    """The store scope for the bot an apply runs against::

        scope_of(ctx)
        # -> ContentScope(env="prod", entity_id="ent_7", bot_id="bot_42")

    Three axes, all read straight off the context. Every receipt this pipeline
    reads and every one it writes is filed under this scope, so they are one
    log. Called by ``fetch``, ``acquire_object``, ``file_bytes`` and
    ``_git_keep_last``.
    """
    return ContentScope(env=ctx.env, entity_id=ctx.entity_id, bot_id=ctx.bot_id)


class EntryFetcher:
    """Fetches one manifest entry's bytes on a bot's behalf.

    The one funnel every fetching category goes through: ``skills``,
    ``resources``, ``identity`` and ``cli_tools``. Four public entry points,
    tabulated in this module's docstring; ``fetch_declared`` is the one a
    materialiser normally calls.

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
        self._content = content
        self._credentials = credentials
        # Required, not defaulted. It was ``Optional[...] = None`` so that
        # rigs driving only the git road need not assemble a store — but
        # the composition root always binds one, so the type said "may be
        # absent" about a value that never is, and bought a ``None`` branch
        # in ``acquire_object`` that production could not reach. Rigs pass
        # ``FakeObjectStore()``; it costs them one line and buys everyone an
        # honest signature.
        self._objects = objects
        # Bound once, to this pipeline: the fetchers are strategies over these
        # same collaborators, so every road files receipts under one policy
        # and W11's lineage cannot answer differently by protocol.
        self._fetchers = build_fetchers(self)

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

    def _git_keep_last(
        self,
        ctx: FetchContext,
        *,
        session: SourceSession,
        spec: GitSourceSpec,
        display: str,
        keep_last: bool,
    ) -> Optional[FetchedEntry]:
        """``keep_last`` for the git road: the receipt of the *last-resolved*
        SHA, when there was one.

        Looks the baseline up by ``display`` and reads the receipt filed under
        ``git+<url>@<baseline sha>:<subpath>``. Answers ``None`` — meaning
        "no fallback, let the failure stand" — in three cases: ``keep_last`` is
        off, the source has no baseline (a first-time source has no stored copy
        entitled to answer for it), or no receipt exists at that address.

        On a hit the :class:`FetchedEntry` carries ``from_store=True`` and a
        ``fallback_reason``, which is what the report's note comes from.
        """
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
        """One object out of a tenant-named bucket, through the object store.

        Takes the resolved ``ObjectStoreTarget`` (bucket plus the endpoint and
        key pair off the credential row) and the already-composed, already
        substituted ``key``. The address it files under is built here::

            acquire_object(ctx,
                           target=ObjectStoreTarget(bucket="team-artifacts",
                                                    ...),
                           key="tools/qc/v2.tgz",
                           digest=None,
                           auth="oss-prod",
                           category="cli_tools",
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
            receipt = self._content.latest_receipt(scope, source_url=address)
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

        limit = FETCH_ENTRY_LIMITS.get(category, FETCH_ENTRY_LIMITS["resources_file"])
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

        ``source_url`` is the caller's own receipt identity, normally the
        ``git+…`` one from ``GitDelivery.receipt_url()``::

            file_bytes(ctx,
                       content=b"acm-tree-v1\n5:a.txt2:hi...",
                       source_url=("git+https://code.example.com/team/"
                                   "content.git@4f2a9c1b...:kb"),
                       category="resources_archive",
                       entry_identity="data/prices/",
                       credential_name="git-prod")
            # -> "sha256:2c26b46b68ffc68ff99b453c1d30413413422d70..."

        ``credential_name`` threads the acquisition's auth into the W11
        lineage exactly as the object road's store call does, so a git-sourced
        receipt answers "which named credential distributed this content"
        the same way an object-sourced one answers it.

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
