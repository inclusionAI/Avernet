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
  never ``is_noop``.
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

_FETCH_CATEGORY = "resources"


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
                source_url = entry.get("source")
                if not isinstance(source_url, str) or not source_url:
                    failures.append(
                        ResolveFailure(
                            path, "a directory entry must declare 'source'"
                        )
                    )
                    continue
                try:
                    archive = await self._fetch_entry(ctx, source_url, entry, path)
                except EntryFetchError as exc:
                    failures.append(ResolveFailure(path, exc.reason))
                    continue
                members = await asyncio.to_thread(
                    self._unpack_members,
                    archive,
                    unpack_kind,
                    entry.get("strip_components", 0),
                )
                if isinstance(members, str):
                    failures.append(ResolveFailure(path, members))
                    continue
                # The directory sentinel: identity=path, value=None. It rides
                # first in the intent list so plan marks the tree for
                # replacement and write deletes it before members upload.
                intents.append(Intent(identity=path, value=None))
                for rel, data in members:
                    intents.append(Intent(identity=path + rel, value=data))
                continue
            inline = entry.get("content")
            if isinstance(inline, str):
                intents.append(Intent(identity=path, value=inline.encode("utf-8")))
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
                value = await self._fetch_entry(ctx, source_url, entry, path)
            except EntryFetchError as exc:
                failures.append(ResolveFailure(str(path), exc.reason))
                continue
            intents.append(Intent(identity=path, value=value))
        self._check_nesting(entries, failures)
        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

    async def _fetch_entry(
        self,
        ctx: ApplyContext,
        source_url: str,
        entry: dict[str, Any],
        path: str,
    ) -> bytes:
        """One entry's bytes (or archive) through the W2/W3/W11 funnel.

        Raises :class:`EntryFetchError` — the caller translates it into this
        category's ``ResolveFailure`` currency. Blocking network + disk I/O
        (W2's sync transport, W11's blob write) off the event loop — see the
        identity materialiser's note; a dry run must not park the server on
        a hung source.
        """
        fetched = await asyncio.to_thread(
            self._fetcher.fetch,
            ctx,
            source_url=source_url,
            digest=entry.get("digest"),
            auth=entry.get("auth"),
            category=_FETCH_CATEGORY,
            keep_last=(entry.get("on_fetch_failure", "keep_last") == "keep_last"),
            entry_identity=path,
        )
        return fetched.content

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
        honour. ``exists`` is consulted only for the created/updated label,
        and the directory sentinels classify within the same vocabulary:
        the orchestrator's dry-run projection feeds every planned outcome
        through :class:`EntryOutcome`, which a bespoke label would crash.
        """
        entity_type, entity_id = _coords(ctx)
        planned: list[PlannedEntry] = []
        for intent in intents:
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
        return CategoryPlan(entries=tuple(planned), removals=())

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
        # 1) Directory sentinels first — one delete per declared tree. A
        # tree's replace removes everything under ``path``, including files
        # the new archive no longer ships and hand-added ones (the
        # ownership rule). Sentinels produce no EntryResult: an ownership
        # action, not an entry.
        for planned in plan.entries:
            if planned.intent.value is not None:
                continue
            await self._resources.delete(
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=ctx.bot_id,
                engine_type=ctx.engine_type,
                path=planned.intent.identity,
            )
        # 2) then each member file, in declaration order
        for planned in plan.entries:
            identity = planned.intent.identity
            data = planned.intent.value
            if data is None:
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
                    self.construct, identity, EntryOutcome(planned.outcome)
                )
            )
        return tuple(results)


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
