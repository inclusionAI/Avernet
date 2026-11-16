"""08-promotion.zh-CN §2、§4、§5、§7 的枚举。"""

from __future__ import annotations

from enum import IntEnum, StrEnum

__all__ = [
    "ActorKind",
    "ApplyKind",
    "CheckName",
    "CheckResult",
    "FloorCheckName",
    "GateOutcome",
    "PatchOpName",
    "PromotableRef",
    "PromotionStatus",
    "RaiseKind",
    "ReviewStatus",
    "RiskTier",
]


class RiskTier(IntEnum):
    """有序，便于比较；JSON 里以名字序列化（"T0".."T3"）。"""

    T0 = 0
    T1 = 1
    T2 = 2
    T3 = 3

    @classmethod
    def parse(cls, value: str | int | None) -> RiskTier | None:
        if value is None:
            return None
        return cls(value) if isinstance(value, int) else cls[str(value)]


class PatchOpName(StrEnum):
    FILE_EDIT = "file.edit"
    PERSONA_ADD = "persona.add"
    PERSONA_REMOVE = "persona.remove"
    SKILL_ADD = "skill.add"
    SKILL_UPDATE = "skill.update"
    SKILL_REMOVE = "skill.remove"
    RESOURCE_ADD = "resource.add"
    RESOURCE_REMOVE = "resource.remove"
    MEMORY_ADD = "memory.add"
    MEMORY_UPDATE = "memory.update"
    MEMORY_RETIRE = "memory.retire"
    MEMORY_SET_MODE = "memory.set_mode"


class RaiseKind(StrEnum):
    REWRITE = "rewrite"
    GUARDRAIL_TOUCH = "guardrail_touch"
    OWNER_OVERRIDE = "owner_override"


class CheckName(StrEnum):
    FLOOR = "floor"
    REGRESSION_FLOOR = "regression_floor"
    VERDICT = "verdict"
    HOLDOUT = "holdout"
    BASELINE = "baseline"
    RISK_TIER_APPROVAL = "risk_tier_approval"


class CheckResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class GateOutcome(StrEnum):
    AUTO_PROMOTE = "auto_promote"
    NEEDS_REVIEW = "needs_review"
    NOT_PROMOTABLE = "not_promotable"


class ReviewStatus(StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"


class PromotionStatus(StrEnum):
    APPLIED = "applied"
    APPLY_FAILED = "apply_failed"


class ActorKind(StrEnum):
    USER = "user"
    GATE_POLICY = "gate_policy"
    ROLLOUT_POLICY = "rollout_policy"
    PIPELINE = "pipeline"


class PromotableRef(StrEnum):
    ACTIVE = "active"
    CANARY = "canary"


class ApplyKind(StrEnum):
    MANIFEST_APPLY = "manifest_apply"
    RELEASE = "release"


class FloorCheckName(StrEnum):
    SCHEMA = "schema"
    BASE = "base"
    MUTABLE_GENES_AND_PINS = "mutable_genes_and_pins"
    ALLOWED_GENES = "allowed_genes"
    SECRET_PII_SCAN = "secret_pii_scan"
    OUTBOUND_ENDPOINTS = "outbound_endpoints"
    PERMISSION_ESCALATION = "permission_escalation"
    SIZE_AND_REWRITE = "size_and_rewrite"
    BUDGET = "budget"
