"""08-promotion.zh-CN §5 的枚举。"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["CheckStatus", "GateOutcome", "ReviewStatus", "RiskTier"]


class RiskTier(StrEnum):
    """补丁的风险等级：按操作取最大值（§5.1）。"""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"

    @property
    def rank(self) -> int:
        return {"T0": 0, "T1": 1, "T2": 2, "T3": 3}[self.value]


class GateOutcome(StrEnum):
    AUTO_PROMOTE = "auto_promote"
    NEEDS_REVIEW = "needs_review"
    NOT_PROMOTABLE = "not_promotable"


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    #: 通过但把候选送去评审（被容忍的回归失败、事件、冻结、达到每日上限）。
    SOFT = "soft"


class ReviewStatus(StrEnum):
    """评审条目的生命周期（08 §2.3、§6.2）。"""

    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    #: 未经决定关闭：它的父修订版已经不是 `active` 了。
    SUPERSEDED = "superseded"
