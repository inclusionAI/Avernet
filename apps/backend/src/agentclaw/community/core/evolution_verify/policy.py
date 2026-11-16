"""默认判定规则与种子升级（07-verification.zh-CN §4、§4.1）。"""

from __future__ import annotations

from .enums import (
    OverfitFlag,
    SanityResult,
    Split,
    VerdictReasonCategory,
    VerdictStatus,
)
from .models import FloorRef, SplitResult, VerificationProfile

__all__ = ["OverfitAssessment", "assess_overfit", "decide", "needs_more_seeds"]


def needs_more_seeds(
    splits: dict[Split, SplitResult], profile: VerificationProfile, seeds_used: int
) -> bool:
    """验证区间在 `escalate_within` 内跨越最小效应时，先加种子再决定。"""

    validation = splits.get(Split.VALIDATION)
    if validation is None or validation.ci is None:
        return False
    if seeds_used >= profile.seeds.max:
        return False
    low, high = validation.ci
    target = profile.validation.min_effect
    return (low < target <= high) and (target - low) <= profile.seeds.escalate_within


def decide(
    floor: FloorRef,
    sanity: SanityResult,
    splits: dict[Split, SplitResult],
    agreement: float | None,
    profile: VerificationProfile,
) -> tuple[VerdictStatus, tuple[VerdictReasonCategory, ...], bool]:
    """纯函数：默认判定规则；返回 (状态, 原因分类, 是否动用回归容差)。"""

    if not floor.passed:
        return VerdictStatus.REJECT, (VerdictReasonCategory.FLOOR_REJECTED,), False
    if sanity is SanityResult.FAIL:
        return VerdictStatus.REJECT, (VerdictReasonCategory.SANITY_FAILED,), False

    safety = splits.get(Split.SAFETY)
    if safety is not None and safety.newly_failing:
        return VerdictStatus.REJECT, (VerdictReasonCategory.MUST_PASS_FAILURE,), False

    regression = splits.get(Split.REGRESSION)
    tolerance_used = False
    if regression is not None and regression.newly_failing:
        if len(regression.newly_failing) > profile.regression_tolerance:
            return (
                VerdictStatus.REJECT,
                (VerdictReasonCategory.MUST_PASS_FAILURE,),
                False,
            )
        tolerance_used = True

    validation = splits.get(Split.VALIDATION)
    if validation is None or validation.ci is None:
        return (
            VerdictStatus.INCONCLUSIVE,
            (VerdictReasonCategory.VALIDATION_INCONCLUSIVE,),
            tolerance_used,
        )

    low, high = validation.ci
    target = profile.validation.min_effect
    if high < target:
        return (
            VerdictStatus.REJECT,
            (VerdictReasonCategory.VALIDATION_NOT_IMPROVED,),
            tolerance_used,
        )

    holdout = splits.get(Split.HOLDOUT)
    if holdout is not None and holdout.ci is not None and holdout.ci[1] < 0:
        return (
            VerdictStatus.REJECT,
            (VerdictReasonCategory.HOLDOUT_DROP,),
            tolerance_used,
        )

    judges_ok = agreement is None or agreement >= profile.min_judge_agreement
    if low >= target and judges_ok:
        return (
            VerdictStatus.ACCEPT,
            (VerdictReasonCategory.VALIDATION_IMPROVED,),
            tolerance_used,
        )
    if not judges_ok:
        return (
            VerdictStatus.INCONCLUSIVE,
            (VerdictReasonCategory.JUDGE_DISAGREEMENT,),
            tolerance_used,
        )
    return (
        VerdictStatus.INCONCLUSIVE,
        (VerdictReasonCategory.VALIDATION_INCONCLUSIVE,),
        tolerance_used,
    )


class OverfitAssessment:
    def __init__(
        self, flags: tuple[OverfitFlag, ...], reasons: tuple[str, ...]
    ) -> None:
        self.flags = flags
        self.reasons = reasons


def assess_overfit(
    splits: dict[Split, SplitResult],
    profile: VerificationProfile,
    per_seed_deltas: dict[Split, list[float]] | None = None,
) -> OverfitAssessment:
    """过拟合防护：验证增益远高于其他划分的增益，或种子间波动过大。"""

    flags: list[OverfitFlag] = []
    reasons: list[str] = []
    validation = splits.get(Split.VALIDATION)
    others = [
        s
        for name, s in splits.items()
        if name is not Split.VALIDATION and s.mean_delta is not None
    ]
    if (
        validation is not None
        and validation.mean_delta is not None
        and validation.mean_delta > 0
        and others
    ):
        best_other = max(s.mean_delta or 0.0 for s in others)
        if best_other <= 0 and validation.mean_delta > 0:
            flags.append(OverfitFlag.GAIN_RATIO_EXCEEDED)
            reasons.append("validation gain has no counterpart on other splits")
        elif (
            best_other > 0
            and validation.mean_delta / best_other > profile.overfit.max_gain_ratio
        ):
            flags.append(OverfitFlag.GAIN_RATIO_EXCEEDED)
            reasons.append("validation gain far exceeds gains elsewhere")
    if per_seed_deltas:
        for split, values in per_seed_deltas.items():
            if len(values) < 2:
                continue
            from statistics import pstdev

            if pstdev(values) > profile.overfit.max_seed_fluctuation:
                flags.append(OverfitFlag.SEED_FLUCTUATION_EXCEEDED)
                reasons.append(f"{split.value} scores fluctuate across seeds")
                break
    return OverfitAssessment(tuple(dict.fromkeys(flags)), tuple(reasons))
