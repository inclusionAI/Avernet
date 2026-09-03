"""The apply orchestrator: every category-level rule, implemented once.

Serialization, ordering, phase selection, the abort rule, the ``skipped``
cascade, the outcome tally — all of it lives here, and a materialiser knows none
of it. That is the whole of what W5, W6 and W13 get from this item: adding a
category is writing a materialiser and adding a registry entry, not rebuilding
any of the above.

**This module must not grow category knowledge.** It names no category and no
section anywhere in its body — a structural test asserts that — because the
moment it does, the registry stops meaning anything and every later work item
adds "just one" special case.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.order import (
    ApplyPhase,
    ApplyStep,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    NO_MATERIALISER_REASON,
    ApplyConstruct,
    ApplyReport,
    CategoryResult,
    EntryOutcome,
    EntryResult,
    derive_status,
    entry_identity,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
    Materialiser,
    ResolveResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestSection,
)
from agentclaw.community.log import get_logger

logger = get_logger()


def declared_entries(
    parsed: Mapping[str, Any], construct: ApplyConstruct
) -> Sequence[dict[str, Any]] | None:
    """What the document declares for one construct, or ``None`` if it does not.

    The distinction this returns is the load-bearing one in §3.2, and it is why
    the return type is ``None``-or-list rather than just a list:

    * ``None`` — **not declared**. No opinion; the area is untouched and nothing
      about it is reported. This is what makes deleting a manifest delete
      nothing: a document with no categories declares nothing, so nothing is
      overwritten.
    * ``[]`` — **declared empty**. A declaration that the set is empty, so the
      area is emptied.

    The two look opposite and are one rule, which is why one test pins both.

    A construct is read from the document by its own name: a section sits at the
    top level, a category under ``manifest``. Which of the two a construct is,
    is its type — this function does not carry a list of names.
    """
    if isinstance(construct, ManifestSection):
        section = parsed.get(construct.value)
        if section is None:
            return None
        # A section is a single object rather than a list; the orchestrator
        # works in entries throughout, so it arrives as a one-entry list. An
        # explicitly-null section is a declaration that there is none.
        return [section] if isinstance(section, dict) else []
    manifest = parsed.get("manifest")
    if not isinstance(manifest, dict) or construct.value not in manifest:
        return None
    entries = manifest.get(construct.value)
    if entries is None:
        return []
    return entries if isinstance(entries, list) else []


class ApplyOrchestrator:
    """Applies a parsed manifest to one bot, category by category.

    Holds no per-apply state: everything is passed in and the report comes back
    out. That is what lets W13 call it twice — once per phase, around container
    provisioning — and get one report from each.
    """

    def __init__(
        self,
        materialisers: Mapping[ApplyConstruct, Materialiser],
        *,
        steps: Callable[[frozenset[ApplyPhase] | None], tuple[ApplyStep, ...]],
    ) -> None:
        self._materialisers = dict(materialisers)
        # Which construct belongs to which phase is the engine family's to say
        # (``apply/delivery.py``, W8): every caller hands the strategy's
        # ``steps_for`` in — there is no default, so the phase table in use is
        # always visible at the call site. The order table's own reading,
        # ``apply.order.steps_for``, is ARCA's.
        self._steps = steps

    async def apply(
        self,
        ctx: ApplyContext,
        parsed: Mapping[str, Any],
        *,
        apply_id: str,
        trigger: str,
        started_at: datetime,
        phases: frozenset[ApplyPhase] | None = None,
        dry_run: bool = False,
    ) -> ApplyReport:
        """Walk the order, apply what is declared, and report what happened.

        ``phases`` selects which half runs; ``None`` is both, which is what an
        apply on an existing bot wants. ``dry_run`` stops each construct after
        its plan, so no write to the BOT or any bot-owned entity occurs —
        "no write of any kind" stopped being true when fetch moved into
        ``resolve`` (W5): a declared source may be fetched, and the bytes the
        platform acquires are filed as its own copy (§2.8's audit trail is
        about acquisition, not delivery); the fetch is bounded by the same
        per-apply ledger a real apply uses. No materialisation, activation
        or removal happens — the write paths are simply never entered.
        """
        results: list[CategoryResult] = []
        for step in self._steps(phases):
            entries = declared_entries(parsed, step.construct)
            if entries is None:
                # Not declared: no opinion, no touch, nothing reported.
                continue
            results.append(
                await self._apply_construct(
                    ctx, step.construct, entries, dry_run=dry_run
                )
            )

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
            # W7: the named sources this apply resolved, read out of the
            # context's session — the orchestrator holds no per-apply state,
            # so this is the only place the report's sources can come from.
            sources=(
                tuple(ctx.source_session.resolution_records())
                if ctx.source_session is not None
                else ()
            ),
        )

    async def _apply_construct(
        self,
        ctx: ApplyContext,
        construct: ApplyConstruct,
        entries: Sequence[dict[str, Any]],
        *,
        dry_run: bool,
    ) -> CategoryResult:
        """One declared construct, through the three stages.

        Every failure path here leaves the construct's area **exactly as it
        was**. That is §3.2's all-or-nothing rule, and under overwrite it is not
        a nicety: writing ``{A}`` when the declaration was ``{A, B}`` deletes B,
        so a partially-materialised category is a *destructive* one.
        """
        materialiser = self._materialisers.get(construct)
        if materialiser is None:
            # An expected state, not a gap: the vocabulary is wider than what
            # this build can act on, and W5/W6 close the window by registering
            # rather than by deleting a branch here. Every entry fails, so the
            # category is aborted and its area is untouched.
            return self._aborted(
                construct, entries, cause=None, reason=NO_MATERIALISER_REASON
            )

        try:
            resolved = await materialiser.resolve(ctx, entries)
        except Exception as exc:  # noqa: BLE001 - one category must not kill the apply
            # A materialiser that raises is a bug, but one category's bug must
            # not stop the others or lose the report. Recorded as an abort,
            # which is the safe reading: nothing was written.
            logger.exception(
                "[manifest_apply] resolve raised, construct=%s, bot_id=%s",
                construct.value,
                ctx.bot_id,
            )
            return self._aborted(
                construct, entries, cause=None, reason=f"resolve failed: {exc}"
            )

        if not resolved.ok:
            return self._aborted(construct, entries, cause=resolved, reason=None)

        try:
            plan = await materialiser.plan(ctx, resolved.intents)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[manifest_apply] plan raised, construct=%s, bot_id=%s",
                construct.value,
                ctx.bot_id,
            )
            return self._aborted(
                construct, entries, cause=None, reason=f"plan failed: {exc}"
            )

        if dry_run:
            # Stop here. The plan is the answer, and no write path has been
            # entered — because the call that would enter one was not made
            # (fetch-side acquisition is resolve's business and may already
            # have happened; writing to the bot is write's, and it is not
            # made).
            return self._projected(construct, plan)

        try:
            written = await materialiser.write(ctx, plan)
        except Exception as exc:  # noqa: BLE001
            # A write that raised may have partially landed. Report it as a
            # failure of every entry rather than claiming any of them: the
            # record must never say something was materialised when it may not
            # have been.
            #
            # ``partially_written`` is the other half of that honesty. Aborting
            # from ``resolve`` or ``plan`` leaves the area untouched; aborting
            # from here does not, and the two are not distinguishable from the
            # entry outcomes alone. A caller told only "aborted" would read the
            # documented "left exactly as it was" and stop — when what it should
            # do is re-apply to converge an area that may now be half-written.
            logger.exception(
                "[manifest_apply] write raised, construct=%s, bot_id=%s",
                construct.value,
                ctx.bot_id,
            )
            return self._aborted(
                construct,
                entries,
                cause=None,
                reason=f"write failed: {exc}",
                partially_written=True,
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
        entries: Sequence[dict[str, Any]],
        *,
        cause: ResolveResult | None,
        reason: str | None,
        partially_written: bool = False,
    ) -> CategoryResult:
        """The category was not written. Report every entry, blame precisely.

        The entry that *caused* the abort is ``FAILED`` with its own reason;
        every other declared entry is ``SKIPPED`` — "not written because its
        category was aborted". Getting that split right is what makes a report
        readable: a caller needs to know which line to fix, not that eight
        things went wrong.

        ``removals`` is empty by construction here: an aborted category is not
        written, so nothing was removed from it.
        """
        failed_by_identity = (
            {failure.identity: failure.reason for failure in cause.failures}
            if cause is not None
            else {}
        )
        results: list[EntryResult] = []
        for index, entry in enumerate(entries):
            identity = entry_identity(entry, index)
            if reason is not None:
                # Whole-category cause (no materialiser, or a stage raised):
                # every entry carries it, because none of them is to blame.
                results.append(
                    EntryResult(construct, identity, EntryOutcome.FAILED, reason)
                )
            elif identity in failed_by_identity:
                results.append(
                    EntryResult(
                        construct,
                        identity,
                        EntryOutcome.FAILED,
                        failed_by_identity[identity],
                    )
                )
            else:
                results.append(
                    EntryResult(
                        construct,
                        identity,
                        EntryOutcome.SKIPPED,
                        "not written: another entry in this category could not "
                        "be materialized, and a category is written in full or "
                        "not at all",
                    )
                )
        return CategoryResult(
            construct=construct,
            entries=tuple(results),
            removals=(),
            aborted=True,
            partially_written=partially_written,
        )

    def _projected(
        self, construct: ApplyConstruct, plan: CategoryPlan
    ) -> CategoryResult:
        """A dry run's answer: the plan, shaped as the result it predicts."""
        return CategoryResult(
            construct=construct,
            entries=tuple(
                EntryResult(
                    construct, planned.intent.identity, EntryOutcome(planned.outcome)
                )
                for planned in plan.entries
            ),
            removals=plan.removals,
            aborted=False,
        )


__all__ = ["ApplyOrchestrator", "declared_entries"]
