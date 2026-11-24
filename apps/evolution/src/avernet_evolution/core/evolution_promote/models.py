"""门禁决策（08 §2.2）：纯数据，可落库、可在评审队列里展示。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .enums import CheckStatus, GateOutcome, ReviewStatus, RiskTier

__all__ = [
    "CandidateFacts",
    "FloorCheck",
    "GateCheck",
    "GateDecision",
    "ReviewItem",
    "decision_from_json",
    "decision_to_json",
    "gate_decision_id",
]


@dataclass(frozen=True)
class GateCheck:
    name: str
    status: CheckStatus
    detail: str = ""


@dataclass(frozen=True)
class CandidateFacts:
    """门禁的输入：判定结果 + 补丁等级 + bot 级软条件。"""

    candidate_id: str
    risk_tier: RiskTier
    verdict_status: str
    regression_failures: int = 0
    regression_tolerance: int = 0
    safety_failures: int = 0
    budget_exceeded: bool = False
    frozen: bool = False
    incident: bool = False


@dataclass(frozen=True)
class GateDecision:
    candidate_id: str
    owner_id: str
    bot_id: str
    run_id: str | None
    outcome: GateOutcome
    risk_tier: RiskTier
    verdict_status: str
    checks: tuple[GateCheck, ...] = ()
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def promotable(self) -> bool:
        return self.outcome is not GateOutcome.NOT_PROMOTABLE


def decision_to_json(decision: GateDecision) -> str:
    return json.dumps(
        {
            "candidate_id": decision.candidate_id,
            "owner_id": decision.owner_id,
            "bot_id": decision.bot_id,
            "run_id": decision.run_id,
            "outcome": decision.outcome.value,
            "risk_tier": decision.risk_tier.value,
            "verdict_status": decision.verdict_status,
            "checks": [
                {"name": check.name, "status": check.status.value, "detail": check.detail}
                for check in decision.checks
            ],
            "decided_at": decision.decided_at.isoformat(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def decision_from_json(text: str) -> GateDecision:
    payload: dict[str, Any] = json.loads(text)
    return GateDecision(
        candidate_id=payload["candidate_id"],
        owner_id=payload["owner_id"],
        bot_id=payload["bot_id"],
        run_id=payload.get("run_id"),
        outcome=GateOutcome(payload["outcome"]),
        risk_tier=RiskTier(payload["risk_tier"]),
        verdict_status=payload["verdict_status"],
        checks=tuple(
            GateCheck(item["name"], CheckStatus(item["status"]), item.get("detail", ""))
            for item in payload.get("checks") or ()
        ),
        decided_at=datetime.fromisoformat(payload["decided_at"]),
    )


def gate_decision_id(candidate_id: str) -> str:
    """门禁决定的稳定标识：由候选内容哈希派生，重算不会换 id（08 §2.2）。"""

    return "gd_" + hashlib.sha256(candidate_id.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class ReviewItem:
    """等待人工决定的候选（08 §2.3）。

    只存决定本身；候选人话（rationale）、证据和策略版本都从实验记录读，
    避免同一事实在两处漂移。
    """

    candidate_id: str
    owner_id: str
    bot_id: str
    revision_id: str
    parent_revision_id: str
    run_id: str
    gate_decision_id: str
    risk_tier: RiskTier
    status: ReviewStatus = ReviewStatus.OPEN
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    promotion_id: str | None = None


@dataclass(frozen=True)
class FloorCheck:
    """静态底线的结果（08 §4.2）。

    `not_run` 不是"通过"：没有扫描器时如实报告，调用方不得把它当成 pass。
    """

    result: str = "not_run"
    problems: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.result == "pass"

    @property
    def failed(self) -> bool:
        return self.result == "fail"
