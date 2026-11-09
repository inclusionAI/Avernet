"""``bot_config`` → per-kind config rows converged onto the destination.

A copy declares one ``DesiredBotConfig`` whose ``config`` is the whole
mapping: per-kind entry lists (``render_screen`` in v1, a future ``task_nav``,
…). The materialiser resolves the whole mapping to one intent, plans one
``created`` step for it, and at write time **dispatches each kind to a
registered handler** through the :class:`BotConfigKindRegistry`. Each handler
does its own overwrite converge against the destination and reports per-row
outcomes, which this materialiser flattens into the ``CategoryWriteResult``
the orchestrator composes — the internal per-kind dispatch the v1 design
exists to keep off the construct layer, so a future kind is one handler, not a
new construct.

The three-stage contract folds to fit a mapping-valued construct, mirroring
the shape ``engine_config`` keeps (one entry holds the whole mapping) while
``engine_config`` has no materialiser yet:

* ``resolve`` — one desired entry → one intent whose ``value`` is the frozen
  kind mapping. Nothing can fail here: the validator already constrained the
  kind set and each kind's entry shape, so a declaration that reached apply is
  one the handlers can act on. The resolve path that ``registry``'s noop
  refusal guards is **drift** — a kind the validator accepts but no DI-bound
  handler resolves at apply time — and that surfaces in ``write``, where a
  real bot is converging and an abort is the honest answer.
* ``plan`` — one intent, one ``created`` planned entry with
  ``requires_write=True``, no removals. The destination read lives in the
  handlers/service, not the materialiser, so the planner cannot classify
  rows; ``write`` is what runs the converge and answers the per-row outcomes.
  This is the ``bot_file_sync`` shape — a single-valued overwrite whose
  idempotency is the handler's, re-established each apply.
* ``write`` — for each declared kind, resolve its handler and call
  ``sync_kind``. Aggregate the handler's per-row outcomes into one
  :class:`~agentclaw.community.core.apply_engine.registry.CategoryWriteResult`:
  rows onto ``entries`` (as ``EntryResult`` keyed by row identity),
  ``removed`` rows onto ``removals``. A kind whose handler reports
  ``failed`` aborts the remaining kinds, whose rows this materialiser marks
  ``SKIPPED`` — the all-or-nothing rule every category honours, held here per
  kind rather than per intent, because the intents are the kinds and a
  partial mapping under overwrite is destructive in the same way a partial
  list is.

Reason text never carries raw exception text: the handler composes its own
report-safe reasons, and the materialiser forwards them verbatim — the same
discipline ``bot_file_sync`` holds.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.desired_state import DesiredBotConfig
from agentclaw.community.core.apply_engine.materialisers.bot_config_kind_registry import (
    BotConfigKindOutcome,
    BotConfigKindRegistry,
)
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
from agentclaw.community.core.apply_engine.constructs import Category


#: The report reason the remaining kinds carry when one kind's handler failed
#: and aborted them. ``SKIPPED`` here is the same meaning it carries in the
#: orchestrator's abort cascade — "not written because a neighbour failed" —
#: applied at the kind granularity, since the kinds are the abort unit.
_KIND_ABORTED_REASON = "bot_config kind '{kind}' was not delivered: an earlier kind failed and aborted the category"


class BotConfigMaterialiser(Materialiser):
    """Converges a copy's whole ``bot_config`` mapping, kind by kind."""

    construct = Category.BOT_CONFIG

    def __init__(self, kind_registry: BotConfigKindRegistry) -> None:
        self._registry = kind_registry

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[DesiredBotConfig]
    ) -> ResolveResult:
        """One desired entry → one intent carrying the frozen kind mapping.

        ``entries`` is the ``Declaration[DesiredBotConfig]``'s entries, of
        which there is exactly one (a mapping-valued Category carries the
        whole mapping on one entry, like ``engine_config``). Nothing fails
        here see the module docstring — the validator already constrained the
        kind set and per-kind shapes, so remaining failure modes surface in
        ``write``.
        """
        intents = tuple(Intent(identity=entry.identity, value=entry.config) for entry in entries)
        return ResolveResult(intents=intents, failures=())

    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """One planned ``created`` step per intent; the handler is the overwrite.

        ``requires_write=True`` so ``CategoryPlan.is_noop`` does not short-circuit
        a mapping whose every row is already converged — the destination read
        lives behind the handler and the materialiser cannot classify rows at
        plan time. ``write`` runs the converge; a wholly-unchanged set returns
        all-``unchanged`` entries and no removals, which the orchestrator
        surfaces as having written nothing even though it observed first.
        """
        planned = tuple(
            PlannedEntry(intent=intent, outcome="created", requires_write=True)
            for intent in intents
        )
        return CategoryPlan(entries=planned, removals=())

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> CategoryWriteResult:
        """Dispatch each declared kind to its handler and flatten the outcomes."""
        results: list[EntryResult] = []
        removals: list[str] = []
        aborted = False
        for planned in plan.entries:
            config: Mapping[str, Sequence[Any]] = planned.intent.value
            if not config:
                # A declared bot_config with no kinds: nothing to converge, no
                # rows to remove (every handler is keyed by a kind). A no-op
                # the report carries as zero entries and zero removals.
                continue
            for kind, kind_entries in config.items():
                if aborted:
                    self._append_aborted(rows=results, removals=removals, kind=kind, entries=kind_entries)
                    continue
                outcome = self._registry.resolve(kind).sync_kind(
                    ctx=ctx,
                    entries=kind_entries,
                    is_empty=len(kind_entries) == 0,
                )
                self._append_kind(rows=results, removals=removals, outcome=outcome)
                if not outcome.ok:
                    aborted = True
        return CategoryWriteResult(tuple(results), tuple(removals))

    @staticmethod
    def _append_kind(
        *,
        rows: list[EntryResult],
        removals: list[str],
        outcome: BotConfigKindOutcome,
    ) -> None:
        for identity, row_outcome, reason in outcome.entries:
            rows.append(
                EntryResult(
                    Category.BOT_CONFIG,
                    identity,
                    EntryOutcome(row_outcome),
                    reason=reason,
                )
            )
        for identity, reason in outcome.removals:
            if reason:
                # A removal that failed: report it as a ``failed`` entry with
                # the row's name, since ``removals`` on the write result are
                # confirmed removals and a failed removal is neither confirmed
                # nor a no-op. The name pins it in the report.
                rows.append(
                    EntryResult(
                        Category.BOT_CONFIG,
                        identity,
                        EntryOutcome.FAILED,
                        reason=reason,
                    )
                )
            else:
                removals.append(identity)

    @staticmethod
    def _append_aborted(
        *,
        rows: list[EntryResult],
        removals: list[str],
        kind: str,
        entries: Sequence[Any],
    ) -> None:
        """Mark a kind the abort reached still-declared rows as ``skipped``."""
        reason = _KIND_ABORTED_REASON.format(kind=kind)
        for index, entry in enumerate(entries):
            identity = (
                entry.get("name") if isinstance(entry, Mapping) else None
            ) or f"{kind}[{index}]"
            rows.append(
                EntryResult(
                    Category.BOT_CONFIG,
                    str(identity),
                    EntryOutcome.SKIPPED,
                    reason=reason,
                )
            )
