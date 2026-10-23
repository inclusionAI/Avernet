"""``resources`` → the resources root (``data/``), cleared then written in full.

**The entry shape.** ``identity`` for this category is the entry's ``path``,
verbatim including any trailing slash. The trailing slash is the **form
discriminator**: ``data/faq.csv`` is a file entry, ``data/prices/`` is a
directory entry. Both arrive typed — ``DesiredResourceFile`` with its stored
bytes, ``DesiredResourceTree`` with its stored ``acm-tree-v1`` blob — the
compiler having read the grammar, fetched, unpacked and filed; every path is
under the resources root, which the Manifest validator and both compilers
enforce.

Three invariants:

- **One write chain for both engine families.** ``ResourceFilePort``'s two
  implementations already differ per transport (arca / baas: device sync;
  teclaw: the managed-files store), so the materialiser never branches on
  engine. **This module must not import anything from
  ``agentclaw.community.kernel.bot_config``** (the artifact contract stays
  untouched).
- **The root is owned whole: clear, then write.** An apply that declares
  resources replaces everything under ``data/`` — a path the previous
  document declared and this one no longer does, and a file the bot wrote
  there itself, both go (#2162). The engine has no "who wrote this" ledger
  and needs none: the root is the ownership boundary, stated in the schema
  docs, and nothing outside it is ever touched. ``plan`` reports what the
  clear will remove as ``removals`` (``listed − declared``), so a dry run
  shows the bot-written file that is about to go.
- **Replace, don't diff.** Every apply rewrites every member — a drifted
  file would survive a source-side comparison — so ``plan`` classifies for
  the report only (``created`` when the path was not listed under the root,
  ``updated`` when it was), never ``unchanged``, and the category is never
  ``is_noop``.

Two things are re-asked here rather than trusted:

- **The write chain's admission predicate is re-asked in ``resolve``** —
  the same :func:`admission_refusal` ``upload_file`` raises (extension
  allow-list, size cap) — so an undeliverable member fails its category
  before the root is cleared, with everything still standing. What is *not*
  re-asked is the HTTP surface's read-only policy (dotfiles, reserved roots):
  a manifest declaring ``data/.env`` is the owner's declaration, deliberately
  broader than the console router's guard.
- **The platform's resource rows follow the files.** After the members are
  written, the bot's file rows under the root are replaced with one row per
  written file (source ``manifest``) through ``ResourceRowsPort``, so the
  console listing and the publish pipeline see what the apply delivered. A
  row write that fails raises ``ConfirmedPartialWriteError``: the files are
  on the bot, the rows are not, and the report says so.

The write window: the transport has no rename, so a stop between the clear
and the last member leaves the root partly written; the member's result row
says ``failed`` and the next apply converges it. Costs: one root listing in
``plan`` (no per-member ``exists`` probes); one tree delete of the root, one
upload per member and one row rewrite in ``write``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Sequence

from agentclaw.community.core.apply_engine.content import ContentReader
from agentclaw.community.core.apply_engine.desired_state import (
    DesiredResource,
    DesiredResourceTree,
    StoredContent,
)
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.content_store import decode_tree_bytes
from agentclaw.community.core.content_store.errors import ContentStoreError
from agentclaw.community.core.apply_engine.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.apply_engine.registry import (
    CategoryPlan,
    ConfirmedPartialWriteError,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.apply_engine.capabilities import (
    ManifestCategory,
)
from agentclaw.community.core.ports.resource_rows_port import ResourceRowsPort
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE

#: The reason every member carries when the root could not be cleared: nothing was written.
ROOT_NOT_CLEARED_REASON = "resources root could not be cleared; nothing was written"


class ResourcesMaterialiser(Materialiser):
    """Converges the resources root toward the declaration.

    ``identity`` is the member's workspace path; ``Intent.value`` is the
    member's ``bytes``. A directory entry fans out into one intent per
    member, so a plan usually carries more entries than the document
    declared::

        # from one entry, path "data/prices/", whose tree holds two files,
        # onto a root that currently holds data/prices/2025.csv and data/notes.txt
        resolve -> ResolveResult(intents=(
                       Intent("data/prices/2026.csv", b"date,price\\n..."),
                       Intent("data/prices/2025.csv", b"date,price\\n...")))
        plan    -> CategoryPlan(entries=(
                       PlannedEntry(<2026>, "created"),
                       PlannedEntry(<2025>, "updated")),
                       removals=("data/notes.txt",))
        write   -> (EntryResult(RESOURCES, "data/prices/2026.csv", CREATED),
                    EntryResult(RESOURCES, "data/prices/2025.csv", UPDATED))

    ``removals`` is what the clear takes away and the declaration does not
    put back — the audit row for the root's ownership rule. ``plan`` never
    answers ``unchanged`` for this category: every apply rewrites every
    member, so the category is never ``is_noop``.
    """

    construct = ManifestCategory.RESOURCES

    def __init__(
        self,
        resource_service: Any,
        content: ContentReader,
        resource_rows: Callable[[str], ResourceRowsPort],
    ) -> None:
        self._resources = resource_service
        self._content = content
        self._rows = resource_rows

    async def resolve(self, ctx: ApplyContext, entries: Sequence[DesiredResource]) -> ResolveResult:
        """Desired entries → intents: bytes per member path, both forms, read from the store.

        Everything that can fail **before touching the bot** and is the
        engine's fails here: a stored blob that is not there, a tree blob that
        does not decode, and the write chain's own admission rule on every
        member — asked now so a refusal never lands after the root has been
        cleared. Path rules, the root, duplicates and nesting are the model's
        and the compiler's, and never reach this stage.
        """
        intents: list[Intent] = []
        failures: list[ResolveFailure] = []
        for entry in entries:
            path = entry.path
            try:
                data = await asyncio.to_thread(self._content.read, _stored(entry).digest)
            except ContentStoreError as exc:
                failures.append(ResolveFailure(path, f"the resource's bytes are not in the store: {exc}"))
                continue
            if isinstance(entry, DesiredResourceTree):
                members = decode_tree_bytes(data)
                if members is None:
                    failures.append(ResolveFailure(path, "the stored directory blob is not an acm-tree-v1 tree"))
                    continue
                # The gate on every member comes before anything is written:
                # one undeliverable member must abort the category with the
                # root still standing. The failure keys to the *declared*
                # entry's identity; the member is named in the reason.
                bad = next(
                    ((path + rel, r) for rel, body in members if (r := _delivery_refusal(path + rel, body))), None
                )
                if bad is not None:
                    failures.append(ResolveFailure(path, f"archive member {bad[0]!r}: {bad[1]}"))
                    continue
                intents.extend(Intent(identity=path + rel, value=body, note=entry.note) for rel, body in members)
                continue
            refused = _delivery_refusal(path, data)
            if refused is not None:
                failures.append(ResolveFailure(path, refused))
                continue
            intents.append(Intent(identity=path, value=data, note=entry.note))
        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    async def plan(self, ctx: ApplyContext, intents: Sequence[Intent]) -> CategoryPlan:
        """Classify against one listing of the root; what the listing has and the declaration lacks is ``removals``.

        Never ``unchanged`` — every apply rewrites every member, so
        classifying anything as unchanged would be a claim the write stage
        does not honour. The listing is consulted for the labels and for the
        removals alone; the write stage clears the root whatever it says.
        """
        entity_type, entity_id, engine = _coords(ctx)
        listed = frozenset(
            await self._resources.list_root(
                entity_type=entity_type, entity_id=entity_id, bot_id=ctx.bot_id, engine_type=engine
            )
        )
        planned = tuple(
            PlannedEntry(intent=intent, outcome="updated" if intent.identity in listed else "created")
            for intent in intents
        )
        declared = {intent.identity for intent in intents}
        return CategoryPlan(entries=planned, removals=tuple(sorted(listed - declared)))

    async def write(self, ctx: ApplyContext, plan: CategoryPlan) -> Sequence[EntryResult]:
        """Clear the root, write every member in declaration order, then replace the rows.

        A clear that fails fails every member with nothing written — the
        root is still what it was. A member whose upload fails gets its own
        ``failed`` row and the rest still land (the transport has no rename,
        so there is no atomic alternative; the report is the source of
        truth). The rows are rewritten last, from the members that landed;
        if that write fails the files are on the bot and the rows are not,
        which is exactly what ``ConfirmedPartialWriteError`` tells the
        orchestrator to report.
        """
        entity_type, entity_id, engine = _coords(ctx)
        coords = dict(entity_type=entity_type, entity_id=entity_id, bot_id=ctx.bot_id, engine_type=engine)
        if not await self._cleared(coords):
            return tuple(
                EntryResult(self.construct, planned.intent.identity, EntryOutcome.FAILED, ROOT_NOT_CLEARED_REASON)
                for planned in plan.entries
            )
        results: list[EntryResult] = []
        written: list[tuple[str, int]] = []
        for planned in plan.entries:
            identity = planned.intent.identity
            data = planned.intent.value
            target_dir, _, filename = identity.rpartition("/")
            try:
                await self._resources.upload_file(**coords, target_dir=target_dir, filename=filename, data=data)
            except Exception:  # noqa: BLE001 — surfaced per entry, not as text
                # Deliberately composed, not interpolated: the report's
                # reason may never carry raw exception text (a transport
                # error can quote a header, a header can carry a token).
                results.append(EntryResult(self.construct, identity, EntryOutcome.FAILED, "resource delivery failed"))
                continue
            written.append((identity, len(data)))
            results.append(
                EntryResult(self.construct, identity, EntryOutcome(planned.outcome), note=planned.intent.note)
            )
        try:
            await self._rows(ctx.bot_id).replace_manifest_rows(files=written, created_by=ctx.actor_id)
        except Exception as exc:
            raise ConfirmedPartialWriteError(
                "the resource files were written but the bot's resource rows could not be replaced"
            ) from exc
        return tuple(results)

    async def _cleared(self, coords: dict[str, str]) -> bool:
        """Whether the root is empty after the clear.

        ``False`` from the port is ambiguous in the write chain's own
        contract: both "there was no root to delete" (a first apply — fine)
        and the transports' only failure signal (every device filesystem
        catches its own errors and answers ``False``). A listing afterwards
        tells them apart: a root that still has files was not cleared. A
        port that raises is a failed clear outright.
        """
        try:
            if await self._resources.clear_root(**coords):
                return True
            return not await self._resources.list_root(**coords)
        except Exception:  # noqa: BLE001 — surfaced per member, in composed words
            return False


def _stored(entry: DesiredResource) -> StoredContent:
    """The one stored blob a resource names: its tree for a directory, its content for a file."""
    return entry.tree if isinstance(entry, DesiredResourceTree) else entry.content


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
