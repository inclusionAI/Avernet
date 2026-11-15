"""``bot_file_sync`` → a per-engine workspace sync, never the content store.

A copy declares one ``DesiredBotFileSync`` (the source bot's id, the literal
``path`` the serializer wrote, the excludes). It converges by **moving the
source bot's workspace tree onto the destination's** through a
:class:`~agentclaw.community.core.ports.bot_file_sync_port.BotFileSyncPort`,
not by fetching bytes into the content store and writing them back per file.
That is the whole reason the construct exists apart from ``resources``:
``resources`` clears the root and writes stored bytes member by member, while a
bot sync is a path-to-path move whose engine-specific shape (NAS rsync,
fastdisk + OSS, …) the materialiser must not know.

The three-stage contract folds to fit a single-valued sync:

* ``resolve`` — one desired entry → one intent whose ``value`` is the sync
  request itself (source bot id, path, excludes). Nothing is read from the
  store: there is nothing to read. The one failure resolve can report is a
  port the DI did not bind, surfaced as a per-entry refusal rather than an
  ImportError at apply time.
* ``plan`` — one intent, one ``created`` planned entry, no removals. A sync
  is overwrite-by-rsync (``--delete --delete-excluded`` at the port), so there
  is no separate "what is there but no longer declared" set to compute.
* ``write`` — call the port, turn ``BotFileSyncResult`` into one
  ``EntryResult`` (``CREATED`` on success, ``FAILED`` with the port's reason
  otherwise). No rows are rewritten: the sync wrote the files in place, and
  the destination's resource rows are reconciled by whatever the engine's
  storage model already does.

The reason field never carries raw exception text — the port returns a
composed reason, the same discipline the resources materialiser holds.
"""
from __future__ import annotations

from typing import Sequence

from agentclaw.community.log import get_logger

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.desired_state import DesiredBotFileSync
from agentclaw.community.core.apply_engine.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.apply_engine.registry import (
    CategoryPlan,
    CategoryWriteResult,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveResult,
)
from agentclaw.community.core.apply_engine.constructs import Section
from agentclaw.community.core.ports.bot_file_sync_port import (
    BotFileSyncPort,
    BotFileSyncTarget,
)

logger = get_logger()


class BotFileSyncMaterialiser(Materialiser):
    """Converges one source bot's workspace onto the destination via a sync port.

    Single-valued by construction (a copy names one source bot), so the
    declaration carries at most one entry; an empty declaration is "sync
    nothing", the same no-op every category honours.
    """

    construct = Section.BOT_FILE_SYNC

    def __init__(self, file_sync: BotFileSyncPort) -> None:
        self._file_sync = file_sync

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[DesiredBotFileSync]
    ) -> ResolveResult:
        """Desired entries → intents. Each entry becomes one sync request."""
        intents = tuple(
            Intent(identity=entry.identity, value=entry) for entry in entries
        )
        return ResolveResult(intents=intents, failures=())

    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """One planned ``created`` entry per intent; a sync overwrites the tree."""
        planned = tuple(
            PlannedEntry(intent=intent, outcome="created") for intent in intents
        )
        return CategoryPlan(entries=planned, removals=())

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> CategoryWriteResult:
        """Run each planned sync, one EntryResult per entry."""
        target = BotFileSyncTarget(
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            entity_id=ctx.entity_id,
            env=ctx.env,
            engine_type=ctx.engine_type,
            entity_type=ctx.bot.get("entity_type") or "staff",
        )
        results: list[EntryResult] = []
        for planned in plan.entries:
            entry: DesiredBotFileSync = planned.intent.value
            try:
                result = await self._file_sync.sync(
                    target=target,
                    source_bot_id=entry.source_bot_id,
                    source_owner_id=entry.source_owner_id,
                    path=entry.path,
                    excludes=entry.excludes,
                )
            except Exception:  # noqa: BLE001 — composed, never raw in the report
                # The report says the catch-all ``"bot file sync failed"``;
                # the log gets the traceback that says what actually failed,
                # so a PARTIAL with no per-branch reason (resolve_build_plan /
                # mkdir / rsync are logged inside the port) can still be traced.
                logger.exception(
                    "bot_file_sync.materialiser_failed bot_id=%s source_bot_id=%s",
                    ctx.bot_id, entry.source_bot_id,
                )
                outcome = EntryOutcome.FAILED
                reason = "bot file sync failed"
            else:
                if result.ok:
                    outcome = EntryOutcome.CREATED
                    reason = None
                else:
                    outcome = EntryOutcome.FAILED
                    reason = result.reason or "bot file sync failed"
            results.append(
                EntryResult(
                    self.construct, planned.intent.identity, outcome, reason=reason
                )
            )
        return CategoryWriteResult(tuple(results), plan.removals)
