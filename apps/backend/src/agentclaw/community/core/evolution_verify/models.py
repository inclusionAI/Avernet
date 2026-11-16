"""07-verification.zh-CN §2.1–§2.6 的模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import (
    CaseCategory,
    CaseFormat,
    CaseOriginKind,
    ExecutorKind,
    OverfitFlag,
    SanityResult,
    ScopeKind,
    Split,
    VerdictReasonCategory,
    VerdictStatus,
)

__all__ = [
    "AggregateScores",
    "Case",
    "CaseOrigin",
    "Cost",
    "CostComparison",
    "FloorRef",
    "Grade",
    "GraderRef",
    "JudgingSpec",
    "RolloutRecord",
    "SeedPolicy",
    "SplitResult",
    "StrategyVerdictView",
    "Suite",
    "SuiteScope",
    "Verdict",
    "VerificationProfile",
]


@dataclass(frozen=True)
class SuiteScope:
    kind: ScopeKind
    bot_owner_id: str | None = None
    bot_id: str | None = None


@dataclass(frozen=True)
class GraderRef:
    id: str
    version: str


@dataclass(frozen=True)
class Suite:
    suite_id: str
    version: int
    scope: SuiteScope
    case_format: CaseFormat
    graders: tuple[GraderRef, ...] = ()
    split_counts: dict[str, int] = field(default_factory=dict)
    digest: str = ""
    created_at: datetime | None = None
    change_reason: str = ""


@dataclass(frozen=True)
class CaseOrigin:
    kind: CaseOriginKind
    run_id: str | None = None


@dataclass(frozen=True)
class JudgingSpec:
    """`grading` 的最小投影：评分器与阈值。"""

    grader: str
    pass_threshold: float = 0.8
    rubric: str = ""


@dataclass(frozen=True)
class Case:
    case_id: str
    suite_id: str
    suite_version: int
    split: Split
    digest: str
    origin: CaseOrigin
    name: str
    category: CaseCategory = CaseCategory.ESCALATION
    timeout_s: int = 120
    grading: JudgingSpec = JudgingSpec(grader="platform/clawbench")
    source_episode: str | None = None


@dataclass(frozen=True)
class Grade:
    score: float
    critique: str = ""
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Cost:
    tokens: int = 0
    usd: float = 0.0
    latency_ms: int = 0


@dataclass(frozen=True)
class RolloutRecord:
    case_id: str
    revision_id: str
    seed: int
    executor: ExecutorKind
    grade: Grade | None = None
    cost: Cost = field(default_factory=Cost)
    transcript_ref: str | None = None


@dataclass(frozen=True)
class CostComparison:
    parent_usd_per_case: float = 0.0
    candidate_usd_per_case: float = 0.0
    increase_pct: float = 0.0
    wins_by_spending: bool = False


@dataclass(frozen=True)
class SeedPolicy:
    default: int = 3
    escalate_within: float = 0.02
    max: int = 5


@dataclass(frozen=True)
class ValidationRule:
    min_effect: float = 0.02
    confidence: float = 0.95


@dataclass(frozen=True)
class OverfitRule:
    max_gain_ratio: float = 3.0
    max_seed_fluctuation: float = 0.15


@dataclass(frozen=True)
class VerificationProfile:
    name: str = "default@1"
    splits: tuple[Split, ...] = (Split.VALIDATION, Split.REGRESSION, Split.SAFETY)
    seeds: SeedPolicy = SeedPolicy()
    validation: ValidationRule = ValidationRule()
    regression_tolerance: int = 1
    min_judge_agreement: float = 0.7
    overfit: OverfitRule = OverfitRule()
    max_cost_increase_pct: float | None = None

    def strictness(self) -> tuple[float, int, int, float]:
        """用于“不得更宽松”比较的排序键：min_effect 越小越宽松。"""

        return (
            self.validation.min_effect,
            -self.regression_tolerance,
            self.seeds.max,
            -self.min_judge_agreement,
        )


@dataclass(frozen=True)
class FloorRef:
    passed: bool
    gate_decision_ref: str | None = None


@dataclass(frozen=True)
class SplitResult:
    cases: int = 0
    seeds: int = 0
    mean_delta: float | None = None
    ci: tuple[float, float] | None = None
    win_rate: float | None = None
    newly_failing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Verdict:
    candidate: str
    bot_owner_id: str
    bot_id: str
    run_id: str | None
    revision: str | None
    parent_revision: str
    profile: str
    status: VerdictStatus
    floor: FloorRef = FloorRef(passed=True)
    sanity: SanityResult = SanityResult.NOT_RUN
    splits: dict[Split, SplitResult] = field(default_factory=dict)
    judge_agreement: float | None = None
    overfit_flags: tuple[OverfitFlag, ...] = ()
    tolerance_used: bool = False
    cost: CostComparison = CostComparison()
    evaluations: tuple[str, ...] = ()
    verifier: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    reasons_categories: tuple[VerdictReasonCategory, ...] = ()
    decided_at: datetime | None = None


@dataclass(frozen=True)
class AggregateScores:
    cases: int
    seeds: int
    mean_delta: float
    ci: tuple[float, float]


@dataclass(frozen=True)
class StrategyVerdictView:
    """策略可见的判定：只有 validation 的聚合值与原因分类。"""

    candidate: str
    status: VerdictStatus
    revision: str | None = None
    aggregates: dict[str, AggregateScores] = field(default_factory=dict)
    reasons: tuple[VerdictReasonCategory, ...] = ()
