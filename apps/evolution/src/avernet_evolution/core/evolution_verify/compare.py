"""比较器：基于评分的纯函数（07-verification.zh-CN §3 第 4 步）。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import mean, pstdev

from .enums import Split
from .models import Grade, RolloutRecord, SplitResult

__all__ = ["compare_split", "newly_failing", "paired_delta"]

_Z_BY_CONFIDENCE = {0.9: 1.645, 0.95: 1.96, 0.99: 2.576}


def _score(rollout: RolloutRecord) -> float | None:
    grade: Grade | None = rollout.grade
    return None if grade is None else grade.score


def paired_delta(candidate: Sequence[RolloutRecord], baseline: Sequence[RolloutRecord]) -> dict[str, float]:
    """按 (case, seed) 配对后每个用例的平均差。"""

    by_key_candidate: dict[tuple[str, int], list[float]] = {}
    by_key_baseline: dict[tuple[str, int], list[float]] = {}
    for rollout in candidate:
        score = _score(rollout)
        if score is not None:
            by_key_candidate.setdefault((rollout.case_id, rollout.seed), []).append(score)
    for rollout in baseline:
        score = _score(rollout)
        if score is not None:
            by_key_baseline.setdefault((rollout.case_id, rollout.seed), []).append(score)

    per_case: dict[str, list[float]] = {}
    for key, scores in by_key_candidate.items():
        if key not in by_key_baseline:
            continue
        delta = mean(scores) - mean(by_key_baseline[key])
        per_case.setdefault(key[0], []).append(delta)
    return {case: mean(deltas) for case, deltas in per_case.items()}


def newly_failing(
    candidate: Sequence[RolloutRecord],
    baseline: Sequence[RolloutRecord],
    threshold: float,
) -> list[str]:
    """父版本通过、候选不通过的用例（必过划分的阻断依据）。"""

    def passed(rollouts: Sequence[RolloutRecord]) -> dict[str, bool]:
        grouped: dict[str, list[float]] = {}
        for rollout in rollouts:
            score = _score(rollout)
            if score is not None:
                grouped.setdefault(rollout.case_id, []).append(score)
        return {case: mean(scores) >= threshold for case, scores in grouped.items()}

    parent, subject = passed(baseline), passed(candidate)
    return sorted(case for case, ok in parent.items() if ok and subject.get(case) is False)


def compare_split(
    split: Split,
    candidate: Sequence[RolloutRecord],
    baseline: Sequence[RolloutRecord],
    *,
    confidence: float = 0.95,
    pass_threshold: float = 0.8,
) -> SplitResult:
    """一个划分的聚合：配对均值差、置信区间、胜率与新失败用例。"""

    deltas = paired_delta(candidate, baseline)
    fails = (
        newly_failing(candidate, baseline, pass_threshold)
        if split in (Split.REGRESSION, Split.SAFETY)
        else []
    )
    cases = len(deltas)
    seeds = len({r.seed for r in candidate}) or 1
    if cases == 0:
        return SplitResult(cases=0, seeds=seeds, newly_failing=fails)

    values = list(deltas.values())
    centre = mean(values)
    spread = pstdev(values) if cases > 1 else 0.0
    z = _Z_BY_CONFIDENCE.get(confidence, 1.96)
    margin = z * spread / math.sqrt(cases) if cases > 1 else 0.0
    win_rate = sum(1 for value in values if value > 0) / cases
    return SplitResult(
        cases=cases,
        seeds=seeds,
        mean_delta=centre,
        ci=(centre - margin, centre + margin),
        win_rate=win_rate,
        newly_failing=fails,
    )
