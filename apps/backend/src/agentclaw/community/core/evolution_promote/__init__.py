"""晋升服务（08-promotion.zh-CN）：静态底线、风险分级、门禁、评审队列与晋升。

它是唯一被允许移动 `active` / `previous` / `canary` 引用的组件（DR-2）。
"""

from __future__ import annotations

from .enums import (
    ActorKind,
    ApplyKind,
    CheckName,
    CheckResult,
    FloorCheckName,
    GateOutcome,
    PatchOpName,
    PromotableRef,
    PromotionStatus,
    RaiseKind,
    ReviewStatus,
    RiskTier,
)
from .errors import (
    AlreadyDecided,
    Drifted,
    EvolutionFrozen,
    NotPromotable,
    NotReviewable,
    PolicyDenied,
    PromotionError,
    RefConflict,
    StaleParent,
    VerdictPending,
)
from .floor import DEFAULT_FLOORS, FloorChecker
from .gate import GateInput, decide
from .models import (
    Actor,
    ApplyResult,
    CandidateReport,
    GateCheck,
    GateDecision,
    OpTier,
    Promotion,
    ReviewItem,
    TierAssessment,
    TierRaise,
)
from .risk import DEFAULT_RULES, classify
from .service import CandidateFacts, LocalPromotionService

__all__ = [
    "Actor",
    "ActorKind",
    "AlreadyDecided",
    "ApplyKind",
    "ApplyResult",
    "CandidateFacts",
    "CandidateReport",
    "CheckName",
    "CheckResult",
    "DEFAULT_FLOORS",
    "DEFAULT_RULES",
    "Drifted",
    "EvolutionFrozen",
    "FloorCheckName",
    "FloorChecker",
    "GateCheck",
    "GateDecision",
    "GateInput",
    "GateOutcome",
    "LocalPromotionService",
    "NotPromotable",
    "NotReviewable",
    "OpTier",
    "PatchOpName",
    "PolicyDenied",
    "PromotableRef",
    "Promotion",
    "PromotionError",
    "PromotionStatus",
    "RaiseKind",
    "RefConflict",
    "ReviewItem",
    "ReviewStatus",
    "RiskTier",
    "StaleParent",
    "TierAssessment",
    "TierRaise",
    "VerdictPending",
    "classify",
    "decide",
]
