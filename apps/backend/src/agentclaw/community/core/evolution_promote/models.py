"""08-promotion.zh-CN §2 的模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import (
    ActorKind,
    ApplyKind,
    CheckName,
    CheckResult,
    GateOutcome,
    PromotableRef,
    PromotionStatus,
    RaiseKind,
    ReviewStatus,
    RiskTier,
)

__all__ = [
    "Actor",
    "ApplyResult",
    "CandidateReport",
    "GateCheck",
    "GateDecision",
    "OpTier",
    "Promotion",
    "ReviewItem",
    "TierAssessment",
    "TierRaise",
]


@dataclass(frozen=True)
class OpTier:
    index: int
    op: str
    genes: tuple[str, ...]
    tier: RiskTier
    condition: str | None = None


@dataclass(frozen=True)
class TierRaise:
    kind: RaiseKind
    detail: str
    from_tier: RiskTier
    to_tier: RiskTier


@dataclass(frozen=True)
class TierAssessment:
    tier: RiskTier
    per_op: tuple[OpTier, ...] = ()
    raises: tuple[TierRaise, ...] = ()


@dataclass(frozen=True)
class GateCheck:
    name: CheckName | str
    result: CheckResult | str
    detail: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateDecision:
    candidate: str
    verdict: str
    risk: TierAssessment
    checks: tuple[GateCheck, ...]
    outcome: GateOutcome
    auto_promote_ceiling: RiskTier | None = RiskTier.T1
    decided_at: datetime | None = None
    detail: str = ""

    @property
    def risk_tier(self) -> RiskTier:
        return self.risk.tier


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    actor_id: str


@dataclass(frozen=True)
class ReviewItem:
    candidate: str
    bot_id: str
    owner_id: str
    tier: RiskTier
    status: ReviewStatus = ReviewStatus.OPEN
    reason: str = ""
    created_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    promotion_id: str | None = None


@dataclass(frozen=True)
class ApplyResult:
    kind: ApplyKind
    ok: bool = True
    detail: str = ""


@dataclass(frozen=True)
class Promotion:
    id: str
    candidate: str | None
    bot_owner_id: str
    bot_id: str
    ref: PromotableRef
    from_revision: str
    to_revision: str
    actor: Actor
    reason: str
    status: PromotionStatus = PromotionStatus.APPLIED
    going_back: bool = False
    apply: ApplyResult | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class CandidateReport:
    candidate: str
    revision: str | None
    parent_revision: str
    patch_summary: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    gate: GateDecision | None = None
    review: ReviewItem | None = None
    promotions: tuple[Promotion, ...] = ()
