"""晋升的拒绝情形（08-promotion.zh-CN §6.2、§7、§9）。"""

from __future__ import annotations

__all__ = [
    "AlreadyDecided",
    "Drifted",
    "EvolutionFrozen",
    "NotPromotable",
    "NotReviewable",
    "PolicyDenied",
    "PromotionError",
    "RefConflict",
    "StaleParent",
    "VerdictPending",
]


class PromotionError(Exception):
    pass


class VerdictPending(PromotionError):
    pass


class NotReviewable(PromotionError):
    pass


class AlreadyDecided(PromotionError):
    pass


class PolicyDenied(PromotionError):
    pass


class StaleParent(PromotionError):
    pass


class EvolutionFrozen(PromotionError):
    pass


class Drifted(PromotionError):
    pass


class RefConflict(PromotionError):
    pass


class NotPromotable(PromotionError):
    pass
