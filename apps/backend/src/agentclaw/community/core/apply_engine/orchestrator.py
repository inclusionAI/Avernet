"""The apply engine's one loop: walk the order, run each materialiser's three stages, report.

**What this module owns.** Serialization, ordering, phase selection, the abort
rule, the ``skipped`` cascade, the outcome tally — all of it lives here, and
a materialiser knows none of it. Adding a construct is writing a materialiser
and a registry entry, not touching any of the above.

**What it takes.** A ``DesiredState`` (``core/apply_engine/desired_state``):
one typed declaration per construct, or ``None`` for a construct the state
does not declare. The compiler that built it already read whatever grammar
it came from and already fetched: a declaration's ``failures`` are what the
compiler could not compile, and they abort the construct exactly as a
materialiser's own ``resolve`` failures do.

**This module must not grow category knowledge.** It names no category and no
section anywhere in its body — a structural test asserts that — because the
moment it does, the registry stops meaning anything and every later work item
adds "just one" special case.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import Declaration, DesiredState
from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.order import ApplyPhase, ApplyStep
from agentclaw.community.core.apply_engine.outcomes import (
    NO_MATERIALISER_REASON,
    ApplyConstruct,
    ApplyReport,
    CategoryResult,
    EntryOutcome,
    EntryResult,
    SourceResolution,
    derive_status,
)
from agentclaw.community.core.apply_engine.registry import (
    CategoryPlan,
    ChecksSetSupply,
    ConfirmedPartialWriteError,
    Materialiser,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: Every declared construct's entries carry this when a declared skill or MCP server is one the bot's skill sets
#: supply: the apply is refused before any write, so nothing in any category was applied.
SET_CONFLICT_REASON = (
    "not applied: the apply was refused because a declared skill or MCP server is supplied by one of the bot's "
    "skill sets"
)

_SKIPPED_REASON = (
    "not written: another entry in this category could not be materialized, and a category is written in full "
    "or not at all"
)


class ApplyOrchestrator:
    """Applies a desired state to one bot, construct by construct.

    Constructed with the registry map (construct → materialiser) and a
    ``steps`` callable, which is always a delivery strategy's ``steps_for``:
    the engine family's phase table applied to the shared order. There is no
    table-level default, so the family whose phases are in use is always
    visible at the call site.

    Holds no per-apply state: everything is passed in and the report comes
    back out. That is what lets the creation job call it twice — once per
    phase, around container provisioning — and get one report from each.
    """

    def __init__(
        self,
        materialisers: Mapping[ApplyConstruct, Materialiser],
        *,
        steps: Callable[[ApplyPhase | None], tuple[ApplyStep, ...]],
    ) -> None:
        self._materialisers = dict(materialisers)
        self._steps = steps

    async def apply(
        self,
        ctx: ApplyContext,
        state: DesiredState,
        *,
        apply_id: str,
        trigger: str,
        started_at: datetime,
        phase: ApplyPhase | None = None,
        dry_run: bool = False,
        sources: tuple[SourceResolution, ...] = (),
    ) -> ApplyReport:
        """Walk the order, apply what the state declares, and report what happened.

        Answers one :class:`~...outcomes.ApplyReport` carrying one
        :class:`~...outcomes.CategoryResult` per **declared** construct — a
        construct the state does not declare contributes no row at all, so a
        report's ``categories`` is usually shorter than the order table.

        ``phase`` selects which half runs; ``None`` is the whole apply, which
        is what an apply on an existing bot wants — only the creation job,
        which delivers one half per call, names one. ``dry_run`` stops each
        construct after its plan, so no write to the bot or any bot-owned
        entity occurs; whatever the compiler fetched and filed on the way to
        the state has already happened, and is bounded the same way a real
        apply's compile is. ``sources`` is what the compiler resolved (the
        Manifest's git sources, W7), carried onto the report.
        """
        # Not declared: no opinion, no touch, nothing reported.
        declared = [
            (step.construct, declaration)
            for step in self._steps(phase)
            if (declaration := state.declaration(step.construct)) is not None
        ]
        conflicts = await self._set_conflicts(ctx, declared)
        results: list[CategoryResult] = []
        for construct, declaration in declared:
            if conflicts:
                results.append(self._refused(construct, declaration, conflicts.get(construct, ())))
            else:
                results.append(await self._apply_construct(ctx, construct, declaration, dry_run=dry_run))

        categories = tuple(results)
        return ApplyReport(
            apply_id=apply_id,
            bot_id=ctx.bot_id,
            trigger=trigger,
            # Derived last, from results every decision has already been made
            # on. Nothing above reads it.
            status=derive_status(categories),
            started_at=started_at,
            finished_at=datetime.now(),
            categories=categories,
            sources=sources,
        )

    async def _set_conflicts(
        self, ctx: ApplyContext, declared: Sequence[tuple[ApplyConstruct, Declaration[Any]]]
    ) -> dict[ApplyConstruct, tuple[ResolveFailure, ...]]:
        """The declared entries the bot's skill sets supply, per construct; empty when the apply may proceed.

        Asked of every construct before any is written. A check that cannot answer refuses the apply too:
        claiming an entry a set may supply is the one thing this stage exists to prevent.
        """
        conflicts: dict[ApplyConstruct, tuple[ResolveFailure, ...]] = {}
        for construct, declaration in declared:
            materialiser = self._materialisers.get(construct)
            if not isinstance(materialiser, ChecksSetSupply) or not declaration.entries:
                continue
            try:
                found = await materialiser.set_conflicts(ctx, declaration.entries)
            except Exception as exc:  # noqa: BLE001 - an unanswered check refuses the apply
                logger.exception(
                    "[manifest_apply] set conflict check raised, construct=%s, bot_id=%s", construct.value, ctx.bot_id
                )
                found = tuple(
                    ResolveFailure(identity, f"could not check the bot's skill sets: {type(exc).__name__}")
                    for identity in declaration.identities
                )
            if found:
                conflicts[construct] = tuple(found)
        return conflicts

    def _refused(
        self, construct: ApplyConstruct, declaration: Declaration[Any], failures: tuple[ResolveFailure, ...]
    ) -> CategoryResult:
        """A construct of an apply refused for set conflicts: nothing written, its own conflicts named."""
        if failures:
            return self._aborted(construct, declaration.identities, cause=ResolveResult(failures=failures), reason=None)
        return self._aborted(construct, declaration.identities, cause=None, reason=SET_CONFLICT_REASON)

    async def _apply_construct(
        self,
        ctx: ApplyContext,
        construct: ApplyConstruct,
        declaration: Declaration[Any],
        *,
        dry_run: bool,
    ) -> CategoryResult:
        """One declared construct, through the three stages.

        The six exits, and what each answers:

        * no materialiser registered → aborted, every entry ``FAILED`` with
          ``NO_MATERIALISER_REASON``;
        * the compiler reported failures → aborted, the named entries
          ``FAILED`` with the compiler's reason, the rest ``SKIPPED``;
        * ``resolve`` raised, or ``plan`` raised → aborted, every entry
          ``FAILED`` with ``"resolve failed: …"`` / ``"plan failed: …"``;
        * ``resolve`` returned failures → aborted, the named entries
          ``FAILED``, their neighbours ``SKIPPED``;
        * ``dry_run`` → the plan, shaped as the result it predicts, not
          aborted;
        * ``write`` raised → aborted **and** ``partially_written=True``.

        Every failure path except the last leaves the construct's area exactly
        as it was. That is the all-or-nothing rule, and under overwrite it is
        not a nicety: writing ``{A}`` when the declaration was ``{A, B}``
        deletes B, so a partially-materialised category is a *destructive* one.
        """
        identities = declaration.identities
        compile_failures = tuple(ResolveFailure(f.identity, f.reason) for f in declaration.failures)
        materialiser = self._materialisers.get(construct)
        if materialiser is None:
            # An expected state, not a gap: the vocabulary is wider than what
            # this build can act on, and the window closes by registering
            # rather than by deleting a branch here. Every entry fails, so the
            # category is aborted and its area is untouched.
            failed = tuple(f.identity for f in compile_failures)
            return self._aborted(construct, failed + identities, cause=None, reason=NO_MATERIALISER_REASON)
        if compile_failures:
            return self._aborted(construct, identities, cause=ResolveResult(failures=compile_failures), reason=None)

        try:
            resolved = await materialiser.resolve(ctx, declaration.entries)
        except Exception as exc:  # noqa: BLE001 - one category must not kill the apply
            # A materialiser that raises is a bug, but one category's bug must
            # not stop the others or lose the report. Recorded as an abort,
            # which is the safe reading: nothing was written.
            logger.exception("[manifest_apply] resolve raised, construct=%s, bot_id=%s", construct.value, ctx.bot_id)
            return self._aborted(construct, identities, cause=None, reason=f"resolve failed: {exc}")

        if not resolved.ok:
            return self._aborted(construct, identities, cause=resolved, reason=None)

        try:
            plan = await materialiser.plan(ctx, resolved.intents)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[manifest_apply] plan raised, construct=%s, bot_id=%s", construct.value, ctx.bot_id)
            return self._aborted(construct, identities, cause=None, reason=f"plan failed: {exc}")

        if dry_run:
            # Stop here. The plan is the answer, and no write path has been
            # entered — because the call that would enter one was not made.
            return self._projected(construct, plan)

        try:
            written = await materialiser.write(ctx, plan)
        except Exception as exc:  # noqa: BLE001
            # A write that raised may have partially landed. Report it as a
            # failure of every entry rather than claiming any of them: the
            # record must never say something was materialised when it may not
            # have been. ``partially_written`` is the other half of that
            # honesty: aborting from ``resolve`` or ``plan`` leaves the area
            # untouched; aborting from here does not, and a caller told only
            # "aborted" would stop when it should re-apply to converge.
            logger.exception("[manifest_apply] write raised, construct=%s, bot_id=%s", construct.value, ctx.bot_id)
            return self._aborted(
                construct,
                identities,
                cause=None,
                reason=f"write failed: {exc}",
                partially_written=True,
                confirmed_partial=isinstance(exc, ConfirmedPartialWriteError),
            )

        return CategoryResult(
            construct=construct,
            entries=tuple(written),
            removals=plan.removals,
            aborted=False,
        )

    def _aborted(
        self,
        construct: ApplyConstruct,
        identities: Sequence[str],
        *,
        cause: ResolveResult | None,
        reason: str | None,
        partially_written: bool = False,
        confirmed_partial: bool = False,
    ) -> CategoryResult:
        """The category was not written. Report every entry, blame precisely.

        Exactly one of ``cause`` and ``reason`` is meaningful. ``reason`` is a
        whole-category cause, so every entry carries it verbatim; ``cause``
        carries per-entry failures, so the named entries get their own reason
        and the rest are ``SKIPPED`` — "not written because its category was
        aborted". Getting that split right is what makes a report readable: a
        caller needs to know which line to fix, not that eight things went
        wrong. An entry the compiler could not compile is not among
        ``identities`` (it never became an entry); its row comes from the
        failure itself, so every declared line still reports.

        ``removals`` is empty by construction here: an aborted category is not
        written, so nothing was removed from it.
        """
        results: list[EntryResult] = []
        if reason is not None:
            results.extend(EntryResult(construct, identity, EntryOutcome.FAILED, reason) for identity in identities)
        else:
            failures = cause.failures if cause is not None else ()
            named = {failure.identity for failure in failures}
            results.extend(EntryResult(construct, f.identity, EntryOutcome.FAILED, f.reason) for f in failures)
            results.extend(
                EntryResult(construct, identity, EntryOutcome.SKIPPED, _SKIPPED_REASON)
                for identity in identities
                if identity not in named
            )
        return CategoryResult(
            construct=construct,
            entries=tuple(results),
            removals=(),
            aborted=True,
            partially_written=partially_written,
            confirmed_partial=confirmed_partial,
        )

    def _projected(self, construct: ApplyConstruct, plan: CategoryPlan) -> CategoryResult:
        """A dry run's answer: the plan, shaped as the result it predicts.

        Each :class:`~...registry.PlannedEntry` becomes an
        :class:`~...outcomes.EntryResult` carrying the same outcome and no
        ``note`` — a projection cannot know what a write would have reported.
        A planned ``skipped`` keeps the reason its materialiser gave, since
        that is known before any write. ``aborted`` is ``False``: a plan that
        could be computed is not a failure.
        """
        return CategoryResult(
            construct=construct,
            entries=tuple(
                EntryResult(construct, planned.intent.identity, EntryOutcome(planned.outcome), reason=planned.reason)
                for planned in plan.entries
            ),
            removals=plan.removals,
            aborted=False,
        )


__all__ = ["ApplyOrchestrator"]
