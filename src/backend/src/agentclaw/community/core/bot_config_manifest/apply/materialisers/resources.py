"""``resources`` → ``ResourceFileService``: workspace files and directory trees.

Three invariants, all from the W6 work item:

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

- **The write chain's admission rules are re-asked in ``resolve``**
  (extension allow-list, size cap — the same constants the service
  enforces), so an undeliverable member fails its category with the tree
  still standing rather than one delete ago. What is *not* re-asked is the
  HTTP surface's read-only policy (dotfiles, reserved roots): a manifest
  declaring ``.env`` is the owner's declaration, deliberately broader than
  the console router's guard — that is the platform's contract with apply,
  not an oversight.
- **``plan`` probes ``exists`` per member** for the report's label alone.
  A 5000-member tree therefore costs 5000 more device round trips than a
  single probe would — accepted for v1's tree sizes, and the first place to
  look if apply latency ever outruns the lock TTL on large archives.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Sequence

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
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
from agentclaw.community.core.bot_config_manifest.fetch.unpack import (
    UnpackError,
    unpack_archive,
)

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

    ``None`` is ``Intent.value``'s dataclass *default*, so keying the tree
    marker on it would let any future intent constructed without an
    explicit value silently promise a whole-tree deletion at write time.
    An instance of a module-private class cannot be produced by accident.
    """

    __slots__ = ()


#: The single marker instance: ``Intent.value`` for a declared directory,
#: identity the declared path with its trailing slash.
_DECLARED_TREE = _DeclaredTree()


class ResourcesMaterialiser(Materialiser):
    """Converges declared workspace resources toward the declaration."""

    construct = ManifestCategory.RESOURCES

    def __init__(self, resource_service: Any, fetcher: Any) -> None:
        self._resources = resource_service
        self._fetcher = fetcher

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
        for index, entry in enumerate(entries):
            path = entry.get("path") if isinstance(entry, dict) else None
            failed = self._entry_failure(entry, path, index)
            if failed is not None:
                failures.append(failed)
                continue
            if isinstance(path, str) and path.endswith("/"):
                unpack_kind = entry.get("unpack")
                if unpack_kind not in ("zip", "tar.gz"):
                    failures.append(
                        ResolveFailure(
                            path,
                            "a directory entry fetched from a URL must declare "
                            "'unpack: zip|tar.gz'",
                        )
                    )
                    continue
                strip = entry.get("strip_components", 0)
                if not isinstance(strip, int) or isinstance(strip, bool) or strip < 0:
                    failures.append(
                        ResolveFailure(
                            path,
                            "'strip_components' must be a non-negative integer",
                        )
                    )
                    continue
                source_url = entry.get("source")
                if not isinstance(source_url, str) or not source_url:
                    failures.append(
                        ResolveFailure(
                            path, "a directory entry must declare 'source'"
                        )
                    )
                    continue
                try:
                    fetched = await self._fetch_entry(
                        ctx, source_url, entry, path, _FETCH_CATEGORY_ARCHIVE
                    )
                    archive = fetched.content
                except EntryFetchError as exc:
                    failures.append(ResolveFailure(path, exc.reason))
                    continue
                members = await asyncio.to_thread(
                    self._unpack_members,
                    archive,
                    unpack_kind,
                    strip,
                )
                if isinstance(members, str):
                    failures.append(ResolveFailure(path, members))
                    continue
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
                    failures.append(ResolveFailure(bad[0], bad[1]))
                    continue
                intents.append(Intent(identity=path, value=_DECLARED_TREE))
                for rel, data in members:
                    intents.append(
                        Intent(
                            identity=path + rel,
                            value=data,
                            note=fetched.fallback_reason,
                        )
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
            source_url = entry.get("source")
            if not isinstance(source_url, str) or not source_url:
                failures.append(
                    ResolveFailure(
                        str(path),
                        "a resources entry must declare 'source' or 'content'",
                    )
                )
                continue
            try:
                fetched = await self._fetch_entry(
                    ctx, source_url, entry, path, _FETCH_CATEGORY_FILE
                )
            except EntryFetchError as exc:
                failures.append(ResolveFailure(str(path), exc.reason))
                continue
            refused = _delivery_refusal(path, fetched.content)
            if refused is not None:
                failures.append(ResolveFailure(path, refused))
                continue
            intents.append(
                Intent(
                    identity=path,
                    value=fetched.content,
                    note=fetched.fallback_reason,
                )
            )
        self._check_nesting(entries, failures)
        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    async def _fetch_entry(
        self,
        ctx: ApplyContext,
        source_url: str,
        entry: dict[str, Any],
        path: str,
        category: str,
    ):
        """One entry's bytes (or archive) through the W2/W3/W11 funnel.

        Raises :class:`EntryFetchError` — the caller translates it into this
        category's ``ResolveFailure`` currency. Returns the whole
        :class:`FetchedEntry` rather than just ``.content``: a keep_last
        fallback's reason rides it (§9.6 — the report row must state the
        fallback), and dropping it here would be the contract broken
        quietly. Blocking network + disk I/O (W2's sync transport, W11's
        blob write) off the event loop — see the identity materialiser's
        note; a dry run must not park the server on a hung source.
        """
        return await asyncio.to_thread(
            self._fetcher.fetch,
            ctx,
            source_url=source_url,
            digest=entry.get("digest"),
            auth=entry.get("auth"),
            category=category,
            keep_last=(entry.get("on_fetch_failure", "keep_last") == "keep_last"),
            entry_identity=path,
        )

    @staticmethod
    def _unpack_members(
        archive: bytes, kind: str, strip_components: int
    ) -> list[tuple[str, bytes]] | str:
        """The guarded unpack, platform-side, into a throwaway directory.

        The bot is never a scratch space: ``unpack_archive`` writes only into
        a fresh temporary directory, so a bad or oversized archive (W1's
        member / unpacked-size limits live inside it) fails before anything
        is delivered. Returned members are ``(relative path, bytes)`` with
        ``strip_components`` already applied — the bytes are read back
        before the throwaway dir goes away; a refusal comes back as its
        reason string rather than an exception, keeping every failure in
        ``resolve``'s currency and the bot's tree untouched.
        """
        try:
            with tempfile.TemporaryDirectory(prefix="manifest-resources-") as tmp:
                tree = unpack_archive(
                    archive,
                    kind,
                    Path(tmp) / "tree",
                    strip_components=strip_components,
                )
                # ``UnpackedTree.members`` are the tree's files only —
                # directories are structural — relative to ``root``.
                return [
                    (name, (tree.root / name).read_bytes()) for name in tree.members
                ]
        except UnpackError as exc:
            return str(exc)

    def _entry_failure(
        self, entry: dict[str, Any], path: Any, index: int
    ) -> ResolveFailure | None:
        """Path re-validation: the belt behind the PUT-time schema rules.

        A stored document can predate a rule, or have skipped the validator
        (a hand-built apply in W8's lifecycle points) — this is the half the
        path-safety question needs answered at *apply* time, not only at
        write time.
        """
        if not isinstance(path, str) or not path:
            return ResolveFailure(
                f"[{index}]", "a resources entry must declare a 'path'"
            )
        if path.startswith("/") or ".." in path.split("/") or "\x00" in path:
            return ResolveFailure(path, "path must be workspace-relative")
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
        entity_type, entity_id = _coords(ctx)
        planned: list[PlannedEntry] = []
        for intent in intents:
            if intent.value is _DECLARED_TREE:
                continue
            present = await self._resources.exists(
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=ctx.bot_id,
                engine_type=ctx.engine_type,
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
        entity_type, entity_id = _coords(ctx)
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
                    engine_type=ctx.engine_type,
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
                    engine_type=ctx.engine_type,
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
                    engine_type=ctx.engine_type,
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
    """The write chain's own admission rules, asked before the first delete.

    ``ResourceFileService.upload_file`` refuses extensions outside its
    allow-list (no ``.sh``, no extensionless files) and content over its
    size cap. A refusal that first lands on the write side would arrive
    *after* the sentinel deleted the declared tree — a deterministically
    half-written tree on every re-apply. So the materialiser re-asks the
    same rules in ``resolve``, where failing costs nothing: the constants
    are imported at call time so this module's importers still pull no
    service graph, and the fake-driven tests cannot drift from the real
    gate because both read the same constants.

    Inline ``content`` is the other reason the gate lives here: it never
    goes through the fetch funnel's caps, so this is the only line an
    oversized inline entry meets.
    """
    from agentclaw.community.core.resources.services.file_service import (
        ALLOWED_EXTENSIONS,
        MAX_FILE_SIZE,
    )

    if Path(identity.rpartition("/")[2]).suffix.lower() not in ALLOWED_EXTENSIONS:
        return (
            "the workspace file surface does not allow this file type; "
            f"allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if len(data) > MAX_FILE_SIZE:
        return (
            "content exceeds the workspace file surface's size cap "
            f"({MAX_FILE_SIZE // (1024 * 1024)}MB)"
        )
    return None


def _coords(ctx: ApplyContext) -> tuple[str, str]:
    """The entity pair every resource write uses, the router's own way.

    The entity is the bot's owner — the address the resources router's
    ``_resolve_params`` resolves and ``resource_coords_from_record`` derives
    — so ``entity_id`` here is ``ctx.owner_id``, **not** ``ctx.entity_id``:
    that field is the manifest's storage key, a different vocabulary that
    happens to share the name. ``entity_type`` is ``"staff"``, the
    personal-bot surface's fixed type.

    The engine halves of the address come from ``ctx.engine_type``, resolved
    once per apply rather than re-resolved here (the context's own rule: a
    single resolution cannot disagree with itself midway through an apply).
    ``resource_coords_from_record`` is therefore not called — it would
    re-resolve the engine through a bot repository this materialiser does
    not carry, for a value the pipeline already holds.
    """
    return "staff", ctx.owner_id


__all__ = ["ResourcesMaterialiser"]
