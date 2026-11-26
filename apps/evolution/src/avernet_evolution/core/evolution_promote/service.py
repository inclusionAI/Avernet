"""08 门禁与评审，不移动 `active`。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from ..evolution_verify.enums import Split, VerdictStatus
from ..evolution_verify.store import VerdictStore
from .enums import GateOutcome, ReviewStatus, RiskTier
from .gate import decide as _decide
from .models import CandidateFacts, FloorCheck, GateDecision, ReviewItem, gate_decision_id
from .store import GateDecisionStore

__all__ = [
    "AlreadyDecided",
    "CandidateNotFound",
    "LocalPromotionService",
    "NotReviewable",
    "PromotionNotReady",
    "ReviewDecision",
    "ReviewNotFound",
    "VerdictPending",
]

_APPLY_DISABLED = "risk review is record-only; explicit human authorization and Apply are a separate control-plane operation"


class VerdictPending(RuntimeError):
    """判定还没落定；门禁不能对 pending 做任何决定（08 §9）。"""


class PromotionNotReady(RuntimeError):
    """候选尚不满足晋升条件，或调用了只记录风险评审的入口。"""


class CandidateNotFound(KeyError):
    """候选不属于该 bot（未知哈希、他人 bot 或从未记录）。"""


class ReviewNotFound(KeyError):
    """候选不在该 bot 的评审队列里（08 §10.1）。"""


class NotReviewable(RuntimeError):
    """候选不需要人工评审：已自动晋升或不可晋升。"""


class AlreadyDecided(RuntimeError):
    """同一候选的评审已经有过决定。"""


@dataclass(frozen=True)
class ReviewDecision:
    review: ReviewItem
    promotion_note: str | None = None


@dataclass
class LocalPromotionService:
    verdicts: VerdictStore
    store: GateDecisionStore
    frozen: Callable[[Any], Awaitable[bool]] | None = None
    incident: Callable[[Any], Awaitable[bool]] | None = None
    #: 没有注入静态扫描器时如实返回 `not_run`。
    floor: Callable[[Any, str], Awaitable[FloorCheck]] | None = None

    async def decide(
        self, bot: Any, candidate_id: str, *, run_id: str, profile: str, ceiling: RiskTier | None = None
    ) -> GateDecision:
        verdict = self.verdicts.load(run_id, candidate_id, profile)
        if verdict is None or (verdict.bot_owner_id, verdict.bot_id) != (bot.owner_id, bot.bot_id):
            raise VerdictPending(candidate_id)
        if verdict.status is VerdictStatus.PENDING:
            raise VerdictPending(candidate_id)

        existing = self.store.get(run_id, candidate_id)
        if existing is not None:
            if (existing.owner_id, existing.bot_id) != (bot.owner_id, bot.bot_id):
                raise CandidateNotFound(candidate_id)
            if existing.outcome is not GateOutcome.NEEDS_REVIEW or self.store.get_review(
                run_id, candidate_id
            ):
                return existing
            # Reconcile historical gate rows whose associated review never committed.
            decision = existing
        else:
            tier = RiskTier(self.store.candidate_risk(candidate_id) or RiskTier.T2.value)
            regression = verdict.splits.get(Split.REGRESSION)
            safety = verdict.splits.get(Split.SAFETY)
            facts = CandidateFacts(
                candidate_id=candidate_id,
                risk_tier=tier,
                verdict_status=verdict.status.value,
                regression_failures=len(regression.newly_failing) if regression else 0,
                safety_failures=len(safety.newly_failing) if safety else 0,
                frozen=await self.frozen(bot) if self.frozen is not None else False,
                incident=await self.incident(bot) if self.incident is not None else False,
            )
            decision = replace(
                _decide(facts, ceiling=ceiling), owner_id=bot.owner_id, bot_id=bot.bot_id, run_id=run_id
            )
        review = None
        if decision.outcome is GateOutcome.NEEDS_REVIEW:
            if verdict.revision is None:
                raise ValueError("review requires a recorded revision")
            review = ReviewItem(
                candidate_id=candidate_id,
                owner_id=bot.owner_id,
                bot_id=bot.bot_id,
                revision_id=verdict.revision,
                parent_revision_id=verdict.parent_revision,
                run_id=run_id,
                gate_decision_id=gate_decision_id(run_id, candidate_id),
                risk_tier=decision.risk_tier,
            )
        return self.store.save(decision, review=review)

    async def check_floor(self, bot: Any, candidate_id: str) -> FloorCheck:
        if self.floor is None:
            return FloorCheck(result="not_run", problems=("no static floor scanner is configured",))
        return await self.floor(bot, candidate_id)

    def queue(self, bot: Any, *, status: ReviewStatus | None = ReviewStatus.OPEN) -> list[ReviewItem]:
        _total, items = self.store.list_reviews(
            owner_id=bot.owner_id, bot_id=bot.bot_id, status=status, page=1, page_size=100
        )
        return items

    def record_review(
        self, bot: Any, candidate_id: str, *, run_id: str, status: ReviewStatus, reason: str, actor: str
    ) -> ReviewDecision:
        """Record a human decision in the caller's SQLite transaction; do not apply."""
        if status not in {ReviewStatus.APPROVED, ReviewStatus.REJECTED} or not reason.strip():
            raise ValueError("review requires an approve/reject decision and a reason")
        decision = self.store.get(run_id, candidate_id)
        if decision is None or (decision.owner_id, decision.bot_id) != (bot.owner_id, bot.bot_id):
            raise ReviewNotFound(candidate_id)
        if decision.outcome is not GateOutcome.NEEDS_REVIEW:
            raise NotReviewable(f"{candidate_id} is {decision.outcome.value}; it is not in the review queue")
        item = self.store.get_review(run_id, candidate_id)
        if item is None or (item.owner_id, item.bot_id) != (bot.owner_id, bot.bot_id):
            raise ReviewNotFound(candidate_id)
        if not self.store.decide_review(
            run_id, candidate_id, status=status, actor=actor, reason=reason.strip()
        ):
            raise AlreadyDecided(candidate_id)
        recorded = self.store.get_review(run_id, candidate_id)
        assert recorded is not None
        return ReviewDecision(recorded, _APPLY_DISABLED if status is ReviewStatus.APPROVED else None)

    async def promote(self, bot: Any, candidate_id: str, *, run_id: str, profile: str) -> GateDecision:
        """切片二：不执行 apply。"""
        decision = await self.decide(bot, candidate_id, run_id=run_id, profile=profile)
        if decision.outcome is not GateOutcome.AUTO_PROMOTE:
            raise PromotionNotReady(
                f"{candidate_id} is {decision.outcome.value}; promotion needs approval first"
            )
        raise PromotionNotReady(_APPLY_DISABLED)
