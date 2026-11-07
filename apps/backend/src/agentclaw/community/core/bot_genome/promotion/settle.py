"""Settling a promotion once its apply has ended: the genome's subscriber to the apply engine's completion publish.

The apply engine publishes the outcome of every apply of the ``genome`` kind
(``core/apply_engine/completion.py``) to ``GenomeCompletionHandler``; the
state reference names the promotion. ``PromotionSettler`` turns the outcome
into the promotion's final status in one repository transaction:

- ``SUCCEEDED`` → ``applied``; ``active`` and ``previous`` move, the revision is ``promoted``;
- ``PARTIAL`` → ``applied_partial``; the same moves, and ``active`` is labelled ``partial``;
- ``FAILED`` → ``apply_failed``; nothing moves.

A lost compare-and-swap (``active`` moved while the apply ran) is recorded as
``apply_failed`` with nothing moved. A repeat notice for a settled promotion
changes nothing, which is what lets the apply engine publish again after a
retry.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from agentclaw.community.core.apply_engine.compiler import StateRef
from agentclaw.community.core.apply_engine.completion import ApplyCompletionHandler, ApplyOutcome
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_genome.models import PromotionStatus, RefLabel
from agentclaw.community.core.bot_genome.repository.models import SettleResult
from agentclaw.community.core.repository.protocols.bot.genome import GenomePromotionRepositoryProtocol
from agentclaw.community.log import get_logger

__all__ = ["GenomeCompletionHandler", "PromotionSettler"]

logger = get_logger()

#: The promotion row's ``apply_detail`` is ``varchar(1024)``.
_DETAIL_CHARS = 1024


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PromotionSettler:
    """One promotion, one final status: the refs move with it or not at all."""

    def __init__(
        self,
        *,
        promotions: GenomePromotionRepositoryProtocol,
        promotable: tuple[str, ...],
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._promotions = promotions
        self._promotable = promotable  # the revision statuses a promotion may mark ``promoted``
        self._now = now

    def settle(
        self, *, env: str, entity_id: str, bot_id: str, promotion_id: str, outcome: ApplyOutcome
    ) -> SettleResult:
        delivered = outcome.status in (ApplyStatus.SUCCEEDED, ApplyStatus.PARTIAL)
        if outcome.status is ApplyStatus.SUCCEEDED:
            status, labels = PromotionStatus.APPLIED, ()
        elif outcome.status is ApplyStatus.PARTIAL:
            status, labels = PromotionStatus.APPLIED_PARTIAL, (RefLabel.PARTIAL.value,)
        else:
            status, labels = PromotionStatus.APPLY_FAILED, ()
        result = self._promotions.settle(
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            promotion_id=promotion_id,
            apply_id=outcome.apply_id,
            status=status.value,
            apply_detail=f"apply {outcome.apply_id}: {outcome.summary}"[:_DETAIL_CHARS],
            move_refs=delivered,
            labels=labels,
            expected_statuses=self._promotable,
            settled_at=self._now(),
        )
        if result is SettleResult.REF_CONFLICT:
            logger.warning(
                "[genome.promotion] active moved while promotion %s was applying; no ref moved", promotion_id
            )
        return result


class GenomeCompletionHandler(ApplyCompletionHandler):
    """The ``genome`` kind's row in the apply engine's completion map."""

    def __init__(self, settler: PromotionSettler) -> None:
        self._settler = settler

    def on_finished(self, *, env: str, entity_id: str, bot_id: str, state: StateRef, outcome: ApplyOutcome) -> None:
        if not state.reference:
            # ``genome_state`` never builds one; a reference that cannot name a promotion has nothing to settle.
            logger.error("[genome.promotion] completion with no promotion reference, apply %s", outcome.apply_id)
            return
        self._settler.settle(env=env, entity_id=entity_id, bot_id=bot_id, promotion_id=state.reference, outcome=outcome)
