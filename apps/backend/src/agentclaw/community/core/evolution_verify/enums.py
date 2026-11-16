"""07-verification.zh-CN §2 的枚举。"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "CallerView",
    "CaseCategory",
    "CaseFormat",
    "CaseOriginKind",
    "EvaluationPurpose",
    "EvaluationStatus",
    "ExecutorKind",
    "HoldoutAuditReason",
    "MUST_PASS_SPLITS",
    "OverfitFlag",
    "SanityResult",
    "ScopeKind",
    "Split",
    "VerdictReasonCategory",
    "VerdictStatus",
]


class Split(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    HOLDOUT = "holdout"
    REGRESSION = "regression"
    SAFETY = "safety"


#: 必须不新失败的划分；`regression` 的容差由配置给出，`safety` 零容差。
MUST_PASS_SPLITS: frozenset[Split] = frozenset({Split.REGRESSION, Split.SAFETY})


class VerdictStatus(StrEnum):
    PENDING = "pending"
    ACCEPT = "accept"
    REJECT = "reject"
    INCONCLUSIVE = "inconclusive"


class SanityResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


class OverfitFlag(StrEnum):
    GAIN_RATIO_EXCEEDED = "gain_ratio_exceeded"
    SEED_FLUCTUATION_EXCEEDED = "seed_fluctuation_exceeded"


class VerdictReasonCategory(StrEnum):
    VALIDATION_IMPROVED = "validation_improved"
    VALIDATION_NOT_IMPROVED = "validation_not_improved"
    VALIDATION_INCONCLUSIVE = "validation_inconclusive"
    JUDGE_DISAGREEMENT = "judge_disagreement"
    MUST_PASS_FAILURE = "must_pass_failure"
    HOLDOUT_DROP = "holdout_drop"
    SANITY_FAILED = "sanity_failed"
    FLOOR_REJECTED = "floor_rejected"


class ExecutorKind(StrEnum):
    LOCAL_SANDBOX = "local_sandbox"
    DEPLOYED_SANDBOX = "deployed_sandbox"


class CaseFormat(StrEnum):
    CLAWBENCH_MD_1 = "clawbench-md@1"


class ScopeKind(StrEnum):
    PLATFORM = "platform"
    BOT = "bot"


class CaseCategory(StrEnum):
    SANITY = "sanity"
    REFUNDS = "refunds"
    RETURNS = "returns"
    SHIPPING = "shipping"
    BILLING = "billing"
    ESCALATION = "escalation"
    SAFETY = "safety"


class CaseOriginKind(StrEnum):
    AUTHORED = "authored"
    MINED = "mined"
    STRATEGY = "strategy"


class EvaluationPurpose(StrEnum):
    VERIFICATION = "verification"
    TRAIN = "train"
    HOLDOUT_AUDIT = "holdout_audit"
    AD_HOC = "ad_hoc"
    SHADOW = "shadow"


class EvaluationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HoldoutAuditReason(StrEnum):
    FINAL_CANDIDATE = "final_candidate"
    PERIODIC = "periodic"


class CallerView(StrEnum):
    """07 §10：调用者能看到逐用例细节的范围。"""

    OPERATOR = "operator"
    REVIEWER = "reviewer"
    STRATEGY = "strategy"
