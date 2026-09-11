"""``resources`` → ``ResourceFileService``: workspace files and directory trees.

**The entry shape.** ``identity`` for this category is the entry's ``path``,
verbatim including any trailing slash. The trailing slash is the **form
discriminator**: ``data/faq.csv`` is a file entry, ``data/prices/`` is a
directory entry. Three source spellings are accepted::

    manifest:
      resources:
        # inline content: no fetch at all
        - path: notes/hello.txt
          content: |
            hello

        # a named source, file form
        - path: data/faq.csv
          from: content
          subpath: faq.csv          # composes with the source's subpath

        # a named source, directory form. Over 'oss' the tree must travel
        # as an archive, so 'unpack' is required; over git it is refused,
        # because a repository hands over a real tree.
        - path: data/prices/
          from: artifacts
          key: prices.tgz
          unpack: tar.gz
          strip_components: 1

An entry reaches ``resolve`` as the raw mapping, e.g. ``{"path":
"data/prices/", "from": "artifacts", "key": "prices.tgz", "unpack":
"tar.gz", "strip_components": 1}``.

Three invariants:

- **One write chain for both engine families.** ``ResourceFileService``'s
  dispatcher already fans out per transport (arca / baas: device sync; teclaw:
  per-file forwarding), so the materialiser never branches on engine — the
  acceptance criterion's "逐文件展开" is a property of this chain, not code
  here. **This module must not import anything from
  ``agentclaw.community.kernel.bot_config``** (the artifact contract stays
  untouched: no directory-typed ``ResourceRef``, no T5 subtree optimisation).
- **Ownership is per-entry.** A file entry owns its exact ``path``; a
  directory entry owns the tree under ``path`` (its replacement removes files
  the new archive no longer ships — including hand-added ones). Nothing
  outside a declared ``path`` is ever touched. Cross-entry removals (a path
  the previous document declared and this one no longer does) are **v1-empty
  by the work item's own definition**: the acceptance criteria define
  ownership only within each entry's tree, and the BaaS transport has no
  "who wrote this file" ledger to answer the broader question — the W12
  contract assigns that breadth to the engine-side applier.
- **Replace, don't diff.** The directory criterion: re-applying an unchanged
  archive must not skip writes based on the *source* looking unchanged — a
  drifted tree would survive that. v1 takes the recommended option (1):
  every apply rewrites every member. ``plan`` therefore classifies for the
  report only (created / updated), never "unchanged", and the category is
  never ``is_noop``. The declared-tree replacement rides the plan's
  ``removals`` channel — the engine's "an overwrite removes something with
  no declared entry to attach to" — so the dry-run projection and the real
  write report one shape, and a dirs-only archive's destructive replace
  still audits through ``removed``.

Two v1 narrows, stated here rather than discovered:

- **The write chain's admission predicate is re-asked in ``resolve``**
  — the same :func:`admission_refusal` ``upload_file`` raises (extension
  allow-list, size cap), so an undeliverable member fails its category
  with the tree still standing rather than one delete ago. What is *not*
  re-asked is the HTTP surface's read-only policy (dotfiles, reserved
  roots): a manifest declaring ``.env`` is the owner's declaration,
  deliberately broader than the console router's guard — that is the
  platform's contract with apply, not an oversight.
- **``plan`` probes ``exists`` per member** for the report's label alone.
  A 5000-member tree therefore costs 5000 more device round trips than a
  single probe would (plus one re-probe per declared tree, on the rare
  path where a tree delete answers ``False``) — accepted for v1's tree
  sizes, and the first place to look if apply latency ever outruns the
  lock TTL on large archives. Follow-up, not fixed here.
"""
from __future__ import annotations

import asyncio
from typing import Any, Sequence

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    EntryDelivery,
    EntryFetchError,
    archive_refusal,
    canonical_tree_bytes,
)
from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
    declared_protocol,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
)
from agentclaw.community.core.bot_config_manifest.schema._support import (
    relative_path_refusal,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import SourceKind
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

#: Schema §5 states the two resource forms' fetch widths separately —
#: ``resources_file`` (100MB) and ``resources_archive`` (200MB) — and the
#: fetch funnel caps by these exact keys, so each form fetches under its own
#: name. A shared "resources" would silently take the file width's fallback
#: for archives, and the W11 linkage column would carry a category the
#: vocabulary never defined. ``FetchCategory``'s members, not raw strings:
#: a typo in a string would silently re-take the fallback cap.
from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FetchCategory,
)

_FETCH_CATEGORY_FILE = FetchCategory.RESOURCES_FILE.value
_FETCH_CATEGORY_ARCHIVE = FetchCategory.RESOURCES_ARCHIVE.value


class _DeclaredTree:
    """The declared-tree marker intent's value — an explicit object, never
    ``None``.

    Carries no data: it exists so ``write`` can tell "delete this whole tree
    first" from an ordinary member write. One directory entry produces one
    marker intent plus one intent per member::

        Intent(identity="data/prices/", value=_DECLARED_TREE)
        Intent(identity="data/prices/2026.csv", value=b"date,price\n...")
        Intent(identity="data/prices/2025.csv", value=b"date,price\n...")

    ``None`` is ``Intent.value``'s dataclass *default*, so keying the tree
    marker on it would let any future intent constructed without an
    explicit value silently promise a whole-tree deletion at write time.
    An instance of a module-private class cannot be produced by accident.
    """

    __slots__ = ()


#: The single marker instance, shared by every declared directory. Compared by
#: identity, so there is deliberately only ever one. ``Intent.value`` for a
#: declared directory; the intent's ``identity`` is the declared path with its
#: trailing slash, e.g. ``"data/prices/"``.
_DECLARED_TREE = _DeclaredTree()


class ResourcesMaterialiser(Materialiser):
    """Converges declared workspace resources toward the declaration.

    ``identity`` is the entry's ``path``; ``Intent.value`` is the member's
    ``bytes``, or :data:`_DECLARED_TREE` for a directory marker. A directory
    entry fans out into several intents, so a plan usually carries more
    entries than the document declared::

        # from one entry, path "data/prices/", whose archive ships two files
        resolve -> ResolveResult(intents=(
                       Intent("data/prices/", _DECLARED_TREE),
                       Intent("data/prices/2026.csv", b"date,price\n..."),
                       Intent("data/prices/2025.csv", b"date,price\n...")))
        plan    -> CategoryPlan(entries=(
                       PlannedEntry(<2026>, "created"),
                       PlannedEntry(<2025>, "created")),
                       removals=("data/prices/",))
        write   -> (EntryResult(RESOURCES, "data/prices/2026.csv", CREATED),
                    EntryResult(RESOURCES, "data/prices/2025.csv", CREATED))

    The marker does **not** become a ``PlannedEntry``: it leaves ``resolve``
    as an intent and leaves ``plan`` as a ``removals`` row, which is what
    gives the destructive tree replacement its audit row.

    ``plan`` never answers ``unchanged`` for this category: every apply
    rewrites every member, so the category is never ``is_noop``.
    """

    construct = ManifestCategory.RESOURCES

    def __init__(self, resource_service: Any, resolver: Any) -> None:
        self._resources = resource_service
        self._resolver = resolver

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """Declared entries → intents: bytes per ``path``, both forms.

        Everything that can fail **before touching the bot** fails here, the
        registry's contract for the whole engine: path validation re-asked
        (a document can predate the rule), inline ``content`` taken as the
        bytes without a fetch, and ``source`` fetched through the W2/W3/W11
        funnel — a failure aborts the whole category before the first write,
        which under overwrite is the non-destructive answer.
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        # The PUT layer refuses a duplicate resource path (one owner per
        # path); the belt re-asks it — two entries at one path would
        # otherwise converge to whatever wrote last, a no-rule answer.
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            path = entry.get("path") if isinstance(entry, dict) else None
            failed = self._entry_failure(entry, path, index)
            if failed is not None:
                failures.append(failed)
                continue
            assert isinstance(path, str)  # _entry_failure passed it
            if path in seen:
                failures.append(
                    ResolveFailure(
                        path,
                        "a resources path is declared more than once "
                        "in this category",
                    )
                )
                continue
            seen.add(path)
            if isinstance(path, str) and path.endswith("/"):
                # The archive fields are checked BEFORE the network, on the one
                # road where they apply. ``resolve``'s whole contract is that
                # everything which can fail without touching the bot fails
                # first — and a fetch here can be 200 MiB against this apply's
                # byte budget and its lock TTL, spent on an entry a missing
                # 'unpack' guarantees to reject. The protocol is answered from
                # the declaration, so no fetch is needed to know which rules
                # apply. ``None`` (a malformed or undeclared source) falls
                # through: the fetch below raises the real error.
                protocol = declared_protocol(ctx, entry)
                if protocol is SourceKind.OSS:
                    refusal = archive_refusal(
                        entry.get("unpack"), entry.get("strip_components", 0)
                    )
                    if refusal is not None:
                        failures.append(ResolveFailure(path, refusal))
                        continue
                try:
                    delivery = await self._fetch_entry(
                        ctx, entry, path, _FETCH_CATEGORY_ARCHIVE
                    )
                    # One call on every road. A git checkout answers with its
                    # tree, an archive with its unpacked members, and a
                    # ``keep_last`` fallback for a failed git fetch answers
                    # with the stored tree decoded — three shapes the delivery
                    # resolves, because which one arrived is not a question
                    # this category has any way to answer.
                    members = await asyncio.to_thread(
                        delivery.members,
                        unpack=entry.get("unpack"),
                        strip_components=entry.get("strip_components", 0),
                    )
                except EntryFetchError as exc:
                    failures.append(ResolveFailure(path, exc.reason))
                    continue
                if isinstance(members, str):
                    failures.append(ResolveFailure(path, members))
                    continue
                # The members are filed as **one canonical blob** under the
                # tree's receipt URL — the same shape the skills materialiser
                # files a package as, and for the same reason: §2.8's audit and
                # ``keep_last`` read one receipt per entry, and a receipt per
                # member would make a 5000-file tree 5000 rows describing one
                # delivery.
                try:
                    await asyncio.to_thread(
                        self._file_tree, ctx, delivery, members, path
                    )
                except EntryFetchError as exc:
                    failures.append(ResolveFailure(path, exc.reason))
                    continue
                note = delivery.note()
                # The declared-tree marker intent rides first so plan routes
                # the tree into ``removals`` and write replaces it before
                # members upload. The gate on every member comes before the
                # marker that promises the tree: one undeliverable member
                # must abort the category with the tree still standing, not
                # delete it for a partial delivery every re-apply repeats.
                bad = next(
                    (
                        (path + rel, refused)
                        for rel, data in members
                        if (refused := _delivery_refusal(path + rel, data))
                        is not None
                    ),
                    None,
                )
                if bad is not None:
                    # The failure keys to the *declared* entry's identity —
                    # the orchestrator's abort mapping blames declared rows,
                    # and a member-keyed failure would match nothing, sinking
                    # the reason (which member, which admission rule) out of
                    # the report. The member is named in the reason instead.
                    failures.append(
                        ResolveFailure(
                            path,
                            f"archive member {bad[0]!r}: {bad[1]}",
                        )
                    )
                    continue
                intents.append(Intent(identity=path, value=_DECLARED_TREE))
                for rel, data in members:
                    intents.append(
                        Intent(identity=path + rel, value=data, note=note)
                    )
                continue
            inline = entry.get("content")
            if isinstance(inline, str):
                data = inline.encode("utf-8")
                refused = _delivery_refusal(path, data)
                if refused is not None:
                    failures.append(ResolveFailure(path, refused))
                    continue
                intents.append(Intent(identity=path, value=data))
                continue
            try:
                delivery = await self._fetch_entry(
                    ctx, entry, path, _FETCH_CATEGORY_FILE
                )
                data = await asyncio.to_thread(delivery.single)
                await asyncio.to_thread(
                    self._file_one, ctx, delivery, data, path
                )
            except EntryFetchError as exc:
                failures.append(ResolveFailure(str(path), exc.reason))
                continue
            note = delivery.note()
            refused = _delivery_refusal(path, data)
            if refused is not None:
                failures.append(ResolveFailure(path, refused))
                continue
            intents.append(Intent(identity=path, value=data, note=note))
        self._check_nesting(entries, failures)
        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    async def _fetch_entry(
        self,
        ctx: ApplyContext,
        entry: dict[str, Any],
        path: str,
        category: str,
    ):
        """One entry's content through the W2/W3/W11 funnel.

        ``DeclaredSourceResolver.resolve``, not ``fetch``: this is the whole of
        defect D1. The
        URL-only call this replaced is why ``resources`` was the one fetching
        category that could not name a source — not just git, but ``from:``
        pointing at anything, since a named source has no ``source:`` URL for
        the old signature to take. Every other fetching category came through
        this door already.

        Returns an :class:`EntryDelivery`; the caller does not branch. The
        whole delivery rather than its bytes, because a keep_last fallback's
        reason and a moved ref's note ride on it (§9.6 — the report row must
        state the fallback), and dropping either here would be the contract
        broken quietly. Blocking network + disk I/O (W2's sync
        transport, W11's blob write) off the event loop — see the identity
        materialiser's note; a dry run must not park the server on a hung
        source.
        """
        return await asyncio.to_thread(
            self._resolver.resolve,
            ctx,
            entry=entry,
            category=category,
            entry_identity=path,
        )

    def _file_tree(
        self,
        ctx: ApplyContext,
        delivery: EntryDelivery,
        members: list[tuple[str, bytes]],
        path: str,
    ) -> None:
        """File a delivered tree with the store, as one canonical blob.

        Unconditional, and the delivery decides what that means: the object
        road filed what arrived inside the fetch and writes nothing again,
        while the git road files these bytes under its own receipt identity
        with the source's credential name riding along — so the lineage
        answers "which credential served this" identically on both roads.
        """
        delivery.file(
            ctx,
            canonical_tree_bytes(members),
            category=_FETCH_CATEGORY_ARCHIVE,
            entry_identity=path,
        )

    def _file_one(
        self,
        ctx: ApplyContext,
        delivery: EntryDelivery,
        data: bytes,
        path: str,
    ) -> None:
        """File one delivered file with the store. Same rule as the tree's."""
        delivery.file(
            ctx,
            data,
            category=_FETCH_CATEGORY_FILE,
            entry_identity=path,
        )

    def _entry_failure(
        self, entry: dict[str, Any], path: Any, index: int
    ) -> ResolveFailure | None:
        """Path re-validation: the belt behind the PUT-time schema rules.

        A stored document can predate a rule, or have skipped the validator
        (a hand-built apply in W8's lifecycle points) — this is the half the
        path-safety question needs answered at *apply* time, not only at
        write time. The rule itself is the schema's own pure predicate
        (:func:`relative_path_refusal`), not a re-derivation: the belt must
        refuse exactly what the PUT layer refuses — "~", drive letters,
        quoted characters and all — or it is a second, weaker rule.
        """
        if not isinstance(path, str) or not path:
            return ResolveFailure(
                f"[{index}]", "a resources entry must declare a 'path'"
            )
        refusal = relative_path_refusal(path, what="path")
        if refusal is not None:
            return ResolveFailure(path, refusal[1])
        return None

    def _check_nesting(
        self,
        entries: Sequence[dict[str, Any]],
        failures: list[ResolveFailure],
    ) -> None:
        """The PUT-time nesting ban, re-asked here (W6 acceptance).

        One declared path living under another declared directory path would
        make the directory's whole-tree replace delete the sibling mid-apply.
        Paths are already relative and normalised at schema time; here we
        re-check, so a document that reached storage before this check existed
        still cannot apply destructively.
        """
        paths = [
            e.get("path")
            for e in entries
            if isinstance(e, dict) and isinstance(e.get("path"), str)
        ]
        directories = [p for p in paths if p.endswith("/")]
        for candidate in paths:
            for directory in directories:
                if candidate != directory and candidate.startswith(directory):
                    failures.append(
                        ResolveFailure(
                            candidate,
                            f"path nests under another declared directory "
                            f"{directory!r}",
                        )
                    )

    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Classify for the report. Never ``unchanged`` — v1 replaces on
        every apply (the work item's recommended option (1)), so classifying
        anything as unchanged would be a claim the write stage does not
        honour. ``exists`` is consulted only for the members' created/updated
        labels.

        The declared trees do not classify at all — they go to the plan's
        ``removals`` channel, the engine's own answer for "an overwrite
        removes something with no declared entry to attach to". That is
        what keeps the dry-run projection and the real write in one shape
        (both take ``plan.removals`` verbatim), and what gives a
        dirs-only archive's destructive replace its audit row.
        """
        entity_type, entity_id, engine = _coords(ctx)
        planned: list[PlannedEntry] = []
        for intent in intents:
            if intent.value is _DECLARED_TREE:
                continue
            present = await self._resources.exists(
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=ctx.bot_id,
                engine_type=engine,
                path=intent.identity,
            )
            planned.append(
                PlannedEntry(
                    intent=intent,
                    outcome="updated" if present else "created",
                )
            )
        removals = tuple(
            intent.identity for intent in intents if intent.value is _DECLARED_TREE
        )
        return CategoryPlan(entries=tuple(planned), removals=removals)

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Execute: replace each declared tree, rewrite each file.

        Half-written windows are v1's documented narrowing (the transport
        has no rename): a mid-write stop leaves the tree in an unknown
        state and the member's result row says ``failed`` — the report is
        the source of truth, no rollback is attempted. The platform-side
        unpack already kept a bad archive from reaching this far.
        """
        entity_type, entity_id, engine = _coords(ctx)
        results: list[EntryResult] = []
        # 1) Declared trees first — one delete per tree, from the plan's
        # removals. A tree's replace removes everything under ``path``,
        # including files the new archive no longer ships and hand-added
        # ones (the ownership rule). Tree deletes are addressed at the path
        # *minus* the declaring slash: the write chain branches file-vs-tree
        # on the path's shape, and "wrap/" reads as a file named "" to that
        # branch. Tree deletes produce no EntryResult: an ownership
        # action, not an entry — but a *failed* one fails its members, in
        # the stage's composed words (never the exception's: a transport
        # error can quote a header, a header can carry a token).
        failed_trees: list[str] = []
        for tree in plan.removals:
            target = tree.rstrip("/")
            try:
                ok = await self._resources.delete(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    bot_id=ctx.bot_id,
                    engine_type=engine,
                    path=target,
                )
            except Exception:  # noqa: BLE001 — surfaced per member, not as text
                failed_trees.append(tree)
                continue
            if ok:
                continue
            # ``False`` is ambiguous in the write chain's own contract: it
            # is both "nothing was deleted" (a first apply onto an absent
            # tree — fine) and the transports' *only* failure signal (every
            # device filesystem catches its own errors and returns False
            # rather than raising). Presence re-probes tell them apart: a
            # tree that is still there was not deleted and never will be,
            # and delivery over an unreplaced tree would report success.
            try:
                still_present = await self._resources.exists(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    bot_id=ctx.bot_id,
                    engine_type=engine,
                    path=target,
                )
            except Exception:  # noqa: BLE001 — pessimistic default below
                still_present = True
            if still_present:
                failed_trees.append(tree)
        # 2) then each member file, in declaration order. Containment is
        # matched against the *declared* form (with the slash) so a tree
        # "wrap/" cannot claim "wrap-old/x.txt" as its member.
        for planned in plan.entries:
            identity = planned.intent.identity
            data = planned.intent.value
            if any(identity.startswith(tree) for tree in failed_trees):
                results.append(
                    EntryResult(
                        self.construct,
                        identity,
                        EntryOutcome.FAILED,
                        "directory tree replacement failed",
                    )
                )
                continue
            target_dir, _, filename = identity.rpartition("/")
            try:
                await self._resources.upload_file(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    bot_id=ctx.bot_id,
                    engine_type=engine,
                    target_dir=target_dir,
                    filename=filename,
                    data=data,
                )
            except Exception:  # noqa: BLE001 — surfaced per entry, not as text
                # Deliberately composed, not interpolated: the report's
                # reason may never carry raw exception text (a transport
                # error can quote a header, a header can carry a token).
                results.append(
                    EntryResult(
                        self.construct,
                        identity,
                        EntryOutcome.FAILED,
                        "resource delivery failed",
                    )
                )
                continue
            results.append(
                EntryResult(
                    self.construct,
                    identity,
                    EntryOutcome(planned.outcome),
                    note=planned.intent.note,
                )
            )
        return tuple(results)


def _delivery_refusal(identity: str, data: bytes) -> str | None:
    """The write chain's own admission predicate, asked before the first delete.

    ``admission_refusal`` (beside the constants in the file-service module)
    is the one rule both surfaces ask: ``upload_file`` raises it, and this
    gate asks it in ``resolve`` so a refusal that would first land on the
    write side never does — write-side refusals arrive *after* the declared
    tree is deleted, a deterministically half-written tree on every
    re-apply. The import stays at call time so this module's importers pull
    no service graph.

    Inline ``content`` is the other reason the gate lives here: it never
    goes through the fetch funnel's caps, so this is the only line an
    oversized inline entry meets.
    """
    from agentclaw.community.core.resources.services.file_service import (
        admission_refusal,
    )

    return admission_refusal(identity.rpartition("/")[2], data)


def _coords(ctx: ApplyContext) -> tuple[str, str, str]:
    """The entity pair — and the routed engine — every resource write uses.

    The entity is the bot's owner — the address the resources router's
    ``_resolve_params`` resolves and ``resource_coords_from_record`` derives
    — so ``entity_id`` here is ``ctx.owner_id``, **not** ``ctx.entity_id``:
    that field is the manifest's storage key, a different vocabulary that
    happens to share the name. ``entity_type`` is ``"staff"``, the
    personal-bot surface's fixed type.

    The engine half routes the same way the router does before it composes
    ``{bot_dir}/{engine}/workspace``: the runtime routing policy
    (``claude_code`` + a non-``normalCC`` template ⇒ ``aicoding``) read
    off the bot record. ``ctx.engine_type`` is the *raw* ``active_engine``
    and the capability vocabulary — redefining it would silently change
    script/`${BOT_ENGINE_TYPE}` substitution — so the routing is applied
    here, over ``ctx.bot`` (carried on the context precisely so a
    materialiser can read engine/template facts off it, its docstring
    records). ``resolve_bot_engine`` is pure over that dict; the import
    stays at call time for the same no-service-graph reason as the
    admission constants (a module-scope import would pull the engine
    registry's strategy graph into every importer). ``resource_coords_from_record``
    is the twin derivation for repo-carrying callers; calling it here would
    re-resolve the engine through a bot repository this materialiser does
    not carry, for a value the carried record already answers.
    """
    from agentclaw.community.core.bot_management.engines.registry import (
        resolve_bot_engine,
    )

    engine = resolve_bot_engine(ctx.bot) or ctx.engine_type or DEFAULT_ENGINE_TYPE
    return "staff", ctx.owner_id, engine


__all__ = ["ResourcesMaterialiser"]
