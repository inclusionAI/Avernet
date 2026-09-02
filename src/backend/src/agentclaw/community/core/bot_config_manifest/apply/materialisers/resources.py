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
from typing import Any, Sequence

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetchError,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    EntryResult,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
    Intent,
    Materialiser,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
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
                # Directory entries arrive with Task 3.
                failures.append(
                    ResolveFailure(
                        str(path),
                        "directory resource entries are not materialised yet",
                    )
                )
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
                # Blocking network + disk I/O (W2's sync transport, W11's blob
                # write) off the event loop — see the identity materialiser's
                # note; a dry run must not park the server on a hung source.
                fetched = await asyncio.to_thread(
                    self._fetcher.fetch,
                    ctx,
                    source_url=source_url,
                    digest=entry.get("digest"),
                    auth=entry.get("auth"),
                    category=_FETCH_CATEGORY,
                    keep_last=(
                        entry.get("on_fetch_failure", "keep_last") == "keep_last"
                    ),
                    entry_identity=path,
                )
            except EntryFetchError as exc:
                failures.append(ResolveFailure(str(path), exc.reason))
                continue
            intents.append(Intent(identity=path, value=fetched.content))
        self._check_nesting(entries, failures)
        return ResolveResult(intents=tuple(intents), failures=tuple(failures))

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
        return CategoryPlan()  # replaced by the classification stage (W6)

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        return ()  # replaced by the delivery stage (W6)


__all__ = ["ResourcesMaterialiser"]
