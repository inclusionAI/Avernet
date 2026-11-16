"""晋升服务（08-promotion.zh-CN §4、§6、§7、§9）。

它是唯一被允许移动 bot 的 `active` / `previous` / `canary` 的组件（DR-2）。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from .enums import (
    ActorKind,
    CheckName,
    CheckResult,
    GateOutcome,
    PromotableRef,
    PromotionStatus,
    ReviewStatus,
    RiskTier,
)
from .errors import (
    AlreadyDecided,
    Drifted,
    NotPromotable,
    NotReviewable,
    PolicyDenied,
    RefConflict,
    StaleParent,
    VerdictPending,
)
from .floor import FloorChecker
from .gate import GateInput, decide as gate_decide
from .models import Actor, CandidateReport, GateDecision, Promotion, ReviewItem

__all__ = ["CandidateFacts", "LocalPromotionService"]


class GenomePort(Protocol):
    async def drift(self, bot: Any) -> Any: ...
    async def resolve_revision(self, bot: Any, ref: str) -> Any: ...
    async def read_content(self, bot: Any, digest: str) -> bytes: ...
    async def move_ref(
        self, bot: Any, ref: str, revision_id: str, actor: str
    ) -> None: ...


@dataclass(frozen=True)
class CandidateFacts:
    """门禁所需的事实；由进化运行与验证服务填充。"""

    candidate: str
    revision: str
    parent_revision: str
    patch: Any = None
    policy: Any = None
    allowed_genes: tuple[str, ...] = ()
    verdict: str = "pending"
    regression_newly_failing: int = 0
    regression_tolerance: int = 1
    safety_newly_failing: int = 0
    holdout_drop: bool = False
    baseline_ok: bool = True
    baseline_required: bool = False
    auto_promote_ceiling: RiskTier | None = RiskTier.T1


@dataclass
class LocalPromotionService:
    genome: GenomePort
    facts: Callable[[str], Awaitable[CandidateFacts]]
    floors: FloorChecker = field(default_factory=FloorChecker)
    incidents: Callable[[Any], Awaitable[bool]] | None = None
    frozen: Callable[[Any], Awaitable[bool]] | None = None
    apply: Callable[[Any, str], Awaitable[Any]] | None = None
    daily_limit: Callable[[Any], Awaitable[bool]] | None = None
    _decisions: dict[str, GateDecision] = field(default_factory=dict, repr=False)
    _reviews: dict[str, ReviewItem] = field(default_factory=dict, repr=False)
    _promotions: dict[str, Promotion] = field(default_factory=dict, repr=False)
    _idempotency: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)

    # -- §4.2 floor -----------------------------------------------------
    async def check_floor(self, bot: Any, candidate_id: str) -> Any:
        facts = await self.facts(candidate_id)
        if facts.patch is None:
            from .models import GateCheck

            return GateCheck(CheckName.FLOOR, CheckResult.PASS, "no patch to inspect")
        checks = self.floors.check(
            facts.patch, facts.policy or _Policy(), facts.allowed_genes
        )
        failed = [check for check in checks if _result(check) is CheckResult.FAIL]
        if failed:
            decision = GateDecision(
                candidate=candidate_id,
                verdict="reject",
                risk=self._risk(facts),
                checks=tuple(checks),
                outcome=GateOutcome.NOT_PROMOTABLE,
                decided_at=_now(),
                detail="static floor failed",
            )
            self._decisions[candidate_id] = decision
            return failed[0]
        from .models import GateCheck

        return GateCheck(CheckName.FLOOR, CheckResult.PASS, "all floor checks passed")

    # -- §4.4 gate ------------------------------------------------------
    async def decide(self, bot: Any, candidate_id: str) -> GateDecision:
        facts = await self.facts(candidate_id)
        if facts.verdict == "pending":
            raise VerdictPending(candidate_id)
        stored = self._decisions.get(candidate_id)
        if stored is not None and stored.verdict != "pending":
            return stored
        floor = await self.check_floor(bot, candidate_id)
        gate_input = GateInput(
            candidate=candidate_id,
            verdict=facts.verdict,
            risk=self._risk(facts),
            floor=(floor,),
            regression_newly_failing=facts.regression_newly_failing,
            regression_tolerance=facts.regression_tolerance,
            safety_newly_failing=facts.safety_newly_failing,
            holdout_drop=facts.holdout_drop,
            baseline_ok=facts.baseline_ok,
            baseline_required=facts.baseline_required,
            auto_promote_ceiling=facts.auto_promote_ceiling,
            incidents_open=await self._incidents(bot),
            frozen=await self._frozen(bot),
            daily_limit_reached=await self._daily(bot),
        )
        decision = gate_decide(gate_input)
        self._decisions[candidate_id] = decision
        if decision.outcome is GateOutcome.AUTO_PROMOTE:
            promotion = await self._promote(
                bot,
                candidate_id,
                facts.revision,
                PromotableRef.ACTIVE,
                facts.parent_revision,
                Actor(ActorKind.GATE_POLICY, "gate_policy"),
                "gate auto-promote",
                f"auto/{candidate_id}",
            )
            self._reviews.pop(candidate_id, None)
            del promotion
        elif decision.outcome is GateOutcome.NEEDS_REVIEW:
            self._reviews.setdefault(
                candidate_id,
                ReviewItem(
                    candidate=candidate_id,
                    bot_id=bot.bot_id,
                    owner_id=bot.owner_id,
                    tier=decision.risk.tier,
                    reason=decision.detail or "risk tier above ceiling",
                    created_at=_now(),
                ),
            )
        return decision

    async def reconsider(self, bot: Any, reason: str) -> list[GateDecision]:
        """bot 级变化后重评开放条目；已决条目永不回到开放状态。"""

        decisions: list[GateDecision] = []
        for candidate_id, item in list(self._reviews.items()):
            if item.status is not ReviewStatus.OPEN:
                continue
            facts = await self.facts(candidate_id)
            if facts.verdict in ("reject", "inconclusive"):
                continue
            decisions.append(await self.decide(bot, candidate_id))
        del reason
        return decisions

    # -- §6 review queue ------------------------------------------------
    async def review_queue(
        self,
        bot: Any,
        status: ReviewStatus | None = ReviewStatus.OPEN,
        min_tier: RiskTier | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[ReviewItem]:
        items = [
            item
            for item in self._reviews.values()
            if item.bot_id == bot.bot_id
            and item.owner_id == bot.owner_id
            and (status is None or item.status is status)
            and (min_tier is None or item.tier >= min_tier)
        ]
        items.sort(key=lambda item: item.tier, reverse=True)
        start = (page - 1) * page_size
        return items[start : start + page_size]

    async def approve(
        self,
        bot: Any,
        candidate_id: str,
        actor: Actor,
        reason: str,
        idempotency_key: str,
        override_stale_parent: bool = False,
    ) -> Promotion:
        item = self._reviews.get(candidate_id)
        if item is None or item.status is not ReviewStatus.OPEN:
            if candidate_id in self._reviews:
                raise AlreadyDecided(candidate_id)
            raise NotReviewable(candidate_id)
        if await self._frozen(bot):
            raise PolicyDenied("evolution is frozen for this bot")
        facts = await self.facts(candidate_id)
        current = await self.genome.resolve_revision(bot, "active")
        active = getattr(getattr(current, "revision", current), "id", None)
        if active != facts.parent_revision and not override_stale_parent:
            raise StaleParent(
                f"active is {active}, candidate's parent is {facts.parent_revision}"
            )
        decision = self._decisions.get(candidate_id)
        if decision is not None and decision.outcome is GateOutcome.NOT_PROMOTABLE:
            raise NotPromotable(candidate_id)
        expected = active if override_stale_parent and active else facts.parent_revision
        promotion = await self._promote(
            bot,
            candidate_id,
            facts.revision,
            PromotableRef.ACTIVE,
            expected,
            actor,
            reason,
            idempotency_key,
        )
        self._reviews[candidate_id] = replace(
            item,
            status=ReviewStatus.APPROVED,
            decided_at=_now(),
            decided_by=actor.actor_id,
            promotion_id=promotion.id,
        )
        return promotion

    async def reject(
        self,
        bot: Any,
        candidate_id: str,
        actor: Actor,
        reason: str,
        idempotency_key: str,
    ) -> ReviewItem:
        item = self._reviews.get(candidate_id)
        if item is None or item.status is not ReviewStatus.OPEN:
            raise NotReviewable(candidate_id)
        stored = self._idempotency.get((candidate_id, idempotency_key))
        if stored is not None:
            return self._reviews[candidate_id]
        del stored
        updated = replace(
            item,
            status=ReviewStatus.REJECTED,
            decided_at=_now(),
            decided_by=actor.actor_id,
            reason=reason,
        )
        self._reviews[candidate_id] = updated
        self._idempotency[(candidate_id, idempotency_key)] = candidate_id
        return updated

    # -- §7 promote -----------------------------------------------------
    async def promote(
        self,
        bot: Any,
        revision_id: str,
        ref: PromotableRef,
        expected_revision: str,
        actor: Actor,
        reason: str,
        idempotency_key: str,
    ) -> Promotion:
        key = (revision_id, idempotency_key)
        if key in self._idempotency:
            return self._promotions[self._idempotency[key]]
        return await self._promote(
            bot,
            None,
            revision_id,
            ref,
            expected_revision,
            actor,
            reason,
            idempotency_key,
        )

    async def _promote(
        self,
        bot: Any,
        candidate_id: str | None,
        revision_id: str,
        ref: PromotableRef,
        expected_revision: str,
        actor: Actor,
        reason: str,
        idempotency_key: str,
    ) -> Promotion:
        key = (revision_id, idempotency_key)
        if key in self._idempotency:
            return self._promotions[self._idempotency[key]]

        drift = await self.genome.drift(bot)
        if drift is not None and actor.kind not in (ActorKind.USER,):
            raise Drifted("bot genome has drifted; only the owner may promote")
        if ref is PromotableRef.ACTIVE:
            current = await self.genome.resolve_revision(bot, "active")
            active = getattr(getattr(current, "revision", current), "id", None)
            if active is not None and active != expected_revision:
                raise RefConflict(f"active is {active}, expected {expected_revision}")

        await self.genome.move_ref(bot, ref.value, revision_id, actor.actor_id)
        apply_result = (
            await self.apply(bot, revision_id) if self.apply is not None else None
        )
        promotion = Promotion(
            id="pr_" + secrets.token_hex(4),
            candidate=candidate_id,
            bot_owner_id=bot.owner_id,
            bot_id=bot.bot_id,
            ref=ref,
            from_revision=expected_revision,
            to_revision=revision_id,
            actor=actor,
            reason=reason,
            status=PromotionStatus.APPLIED
            if (apply_result is None or apply_result.ok)
            else PromotionStatus.APPLY_FAILED,
            going_back=False,
            apply=apply_result,
            created_at=_now(),
        )
        self._promotions[promotion.id] = promotion
        self._idempotency[key] = promotion.id
        return promotion

    async def candidate_report(
        self, bot: Any, candidate_id: str, viewer: Actor
    ) -> CandidateReport:
        facts = await self.facts(candidate_id)
        return CandidateReport(
            candidate=candidate_id,
            revision=facts.revision,
            parent_revision=facts.parent_revision,
            patch_summary={
                "ops": len(getattr(facts.patch, "ops", ()) or ()),
                "rationale": getattr(facts.patch, "rationale", ""),
            },
            verification={"verdict": facts.verdict},
            gate=self._decisions.get(candidate_id),
            review=self._reviews.get(candidate_id),
            promotions=tuple(
                p for p in self._promotions.values() if p.candidate == candidate_id
            ),
        )

    # -- helpers --------------------------------------------------------
    def _risk(self, facts: CandidateFacts):
        from .risk import classify

        if facts.patch is None:
            from .models import TierAssessment

            return TierAssessment(tier=RiskTier.T0)
        return classify(facts.patch, facts.policy)

    async def _incidents(self, bot: Any) -> bool:
        return bool(await self.incidents(bot)) if self.incidents is not None else False

    async def _frozen(self, bot: Any) -> bool:
        return bool(await self.frozen(bot)) if self.frozen is not None else False

    async def _daily(self, bot: Any) -> bool:
        return (
            bool(await self.daily_limit(bot)) if self.daily_limit is not None else False
        )


class _Policy:
    mutable_genes: tuple[str, ...] = ()
    pins: tuple[str, ...] = ()
    risk_overrides: tuple[()] = ()


def _result(check: Any) -> CheckResult:
    return (
        check.result
        if isinstance(check.result, CheckResult)
        else CheckResult(check.result)
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
