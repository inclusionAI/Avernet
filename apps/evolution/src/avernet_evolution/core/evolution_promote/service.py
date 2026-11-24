"""08 的服务门面：门禁决策 + 评审队列（切片二/三：不移动 `active`）。

真正晋升需要 revision 捕获 bot 的完整状态；在那之前 `promote()` 会显式失败，
而不是把 bot 覆盖成不完整快照。评审的**决定**照常记录：
"人已经批了" 与 "apply 已经发生" 是两件独立的事。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..evolution_verify.enums import Split
from ..evolution_verify.models import Verdict
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


class VerdictPending(RuntimeError):
    """判定还没落定；门禁不能对 pending 做任何决定（08 §9）。"""


class PromotionNotReady(RuntimeError):
    """08 的 apply 侧尚未启用：revision 还不是 bot 的完整快照。"""


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
    promotion_id: str | None = None
    promotion_note: str | None = None


@dataclass
class LocalPromotionService:
    verdicts: Any  # VerdictStore：for_candidate(candidate_id) -> list[Verdict]
    store: GateDecisionStore
    ceiling_for: Callable[[Any], Awaitable[RiskTier | None]] | None = None
    frozen: Callable[[Any], Awaitable[bool]] | None = None
    incident: Callable[[Any], Awaitable[bool]] | None = None
    #: 静态底线（08 §4.2）。没有注入扫描器时如实返回 `not_run`。
    floor: Callable[[Any, str], Awaitable[FloorCheck]] | None = None
    applied: list[str] = field(default_factory=list, repr=False)

    async def decide(self, bot: Any, candidate_id: str) -> GateDecision:
        existing = self.store.get(candidate_id)
        if existing is not None:
            return existing

        verdict = self._verdict_for(candidate_id)
        if verdict is None or verdict.status.value == "pending":
            raise VerdictPending(candidate_id)

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
        ceiling = await self.ceiling_for(bot) if self.ceiling_for is not None else None
        decision = _decide(facts, ceiling=ceiling)
        decision = GateDecision(
            candidate_id=decision.candidate_id,
            owner_id=getattr(bot, "owner_id", ""),
            bot_id=getattr(bot, "bot_id", ""),
            run_id=verdict.run_id,
            outcome=decision.outcome,
            risk_tier=decision.risk_tier,
            verdict_status=decision.verdict_status,
            checks=decision.checks,
            decided_at=decision.decided_at,
        )
        self.store.save(decision)
        if decision.outcome is GateOutcome.NEEDS_REVIEW:
            # 队列在门禁做出决定时就存在，UI 才能立即看到待办。
            self.store.open_review(
                ReviewItem(
                    candidate_id=candidate_id,
                    owner_id=decision.owner_id,
                    bot_id=decision.bot_id,
                    revision_id=verdict.revision or "",
                    parent_revision_id=verdict.parent_revision or "",
                    run_id=verdict.run_id or "",
                    gate_decision_id=gate_decision_id(candidate_id),
                    risk_tier=decision.risk_tier,
                )
            )
        return decision

    async def check_floor(self, bot: Any, candidate_id: str) -> FloorCheck:
        """提交时的静态底线。

        结构、可变基因、允许的基因与大小限制已由提交路径和 Genome 注册表强制；
        密钥/PII/URL 扫描器尚未接入，因此这里明确报告 `not_run`，不假装通过。
        """

        if self.floor is None:
            return FloorCheck(result="not_run", problems=("no static floor scanner is configured",))
        return await self.floor(bot, candidate_id)

    def queue(self, bot: Any, *, status: ReviewStatus | None = ReviewStatus.OPEN) -> list[ReviewItem]:
        _total, items = self.store.list_reviews(
            owner_id=bot.owner_id, bot_id=bot.bot_id, status=status, page=1, page_size=100
        )
        return items

    async def review(self, bot: Any, candidate_id: str) -> tuple[ReviewItem, GateDecision]:
        """读视图：评审条目 + 它对应的门禁决定。"""

        item = self.store.get_review(candidate_id)
        decision = self.store.get(candidate_id)
        if item is None or decision is None or item.owner_id != bot.owner_id or item.bot_id != bot.bot_id:
            raise ReviewNotFound(candidate_id)
        return item, decision

    async def approve(
        self,
        bot: Any,
        candidate_id: str,
        *,
        reason: str,
        actor: str,
    ) -> ReviewDecision:
        self._decidable(bot, candidate_id)
        if not reason.strip():
            raise ValueError("approval requires a reason")
        # 先记录人的决定，再尝试 apply：apply 失败不应抹掉审计里"谁批了"。
        if not self.store.decide_review(
            candidate_id, status=ReviewStatus.APPROVED, actor=actor, reason=reason.strip()
        ):
            raise AlreadyDecided(candidate_id)
        note = None
        try:
            await self._apply(bot, candidate_id, actor=actor, reason=reason.strip())
        except PromotionNotReady as exc:
            note = str(exc)
        approved, _ = await self.review(bot, candidate_id)
        return ReviewDecision(review=approved, promotion_id=approved.promotion_id, promotion_note=note)

    async def reject(self, bot: Any, candidate_id: str, *, reason: str, actor: str) -> ReviewDecision:
        self._decidable(bot, candidate_id)
        if not reason.strip():
            raise ValueError("rejection requires a reason")
        if not self.store.decide_review(
            candidate_id, status=ReviewStatus.REJECTED, actor=actor, reason=reason.strip()
        ):
            raise AlreadyDecided(candidate_id)
        rejected, _ = await self.review(bot, candidate_id)
        return ReviewDecision(review=rejected)

    async def promote(self, bot: Any, candidate_id: str) -> GateDecision:
        """切片二：只允许 auto_promote 的候选走到这里，并明确拒绝执行 apply。"""

        decision = await self.decide(bot, candidate_id)
        if decision.outcome is not GateOutcome.AUTO_PROMOTE:
            raise PromotionNotReady(
                f"{candidate_id} is {decision.outcome.value}; promotion needs approval first"
            )
        raise PromotionNotReady(
            "promotion apply is disabled until a revision captures the bot's complete state"
        )

    async def _apply(self, bot: Any, candidate_id: str, *, actor: str, reason: str) -> str:
        del bot, candidate_id, actor, reason
        raise PromotionNotReady(
            "promotion apply is disabled until a revision captures the bot's complete state"
        )

    def _decidable(self, bot: Any, candidate_id: str) -> ReviewItem:
        """批准/拒绝的前置条件：有决定、在队列里、还是 open。"""

        decision = self.store.get(candidate_id)
        if decision is None:
            raise ReviewNotFound(candidate_id)
        item = self.store.get_review(candidate_id)
        if item is None or item.owner_id != bot.owner_id or item.bot_id != bot.bot_id:
            # 已自动晋升或不可晋升的候选不会开单（08 §4.3）。
            raise NotReviewable(f"{candidate_id} is {decision.outcome.value}; it is not in the review queue")
        if item.status is not ReviewStatus.OPEN:
            raise AlreadyDecided(candidate_id)
        if decision.outcome is GateOutcome.NOT_PROMOTABLE:
            raise NotReviewable(f"{candidate_id} is not_promotable")
        return item

    def _verdict_for(self, candidate_id: str) -> Verdict | None:
        verdicts = self.verdicts.for_candidate(candidate_id)
        if not verdicts:
            return None
        return next((v for v in verdicts if v.profile == "default@1"), verdicts[0])
