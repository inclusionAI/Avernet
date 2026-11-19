"""验证服务（07-verification.zh-CN §4、§9）。

`verify` 立即返回 pending 判定并在后台跑流水线；判定按 (候选, 配置) 幂等，
最终值永不改变。逐用例细节只经 `get_evaluation` 对运维可见，
`strategy_view` 只给 validation 聚合。
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from .compare import compare_split
from .enums import (
    CallerView,
    HoldoutAuditReason,
    SanityResult,
    Split,
    VerdictReasonCategory,
    VerdictStatus,
)
from .models import (
    AggregateScores,
    Case,
    CostComparison,
    Grade,
    RolloutRecord,
    SplitResult,
    StrategyVerdictView,
    Verdict,
    VerificationProfile,
)
from .policy import assess_overfit, decide, needs_more_seeds

__all__ = ["CandidateRef", "Evaluation", "LocalVerificationService"]


@dataclass(frozen=True)
class CandidateRef:
    candidate_id: str
    bot_owner_id: str
    bot_id: str
    revision: str
    parent_revision: str
    run_id: str | None = None


@dataclass(frozen=True)
class Evaluation:
    evaluation_id: str
    purpose: str
    subject: str
    baseline: str | None
    status: str
    rollouts: tuple[RolloutRecord, ...] = ()
    run_id: str | None = None


@dataclass
class LocalVerificationService:
    """本地验证器：执行器与评分器由组合根注入，判定规则是纯函数。"""

    profiles: dict[str, VerificationProfile]
    cases_for: Callable[[Any, Sequence[Split]], Awaitable[Sequence[Case]]]
    execute: Callable[[Any, str, Case, int], Awaitable[RolloutRecord]]
    grade: Callable[[Case, RolloutRecord], Awaitable[Grade]]
    agreement: Callable[[Sequence[RolloutRecord]], Awaitable[float | None]] | None = None
    default_profile: str = "default@1"
    _verdicts: dict[tuple[str, str], Verdict] = field(default_factory=dict, repr=False)
    _evaluations: dict[str, Evaluation] = field(default_factory=dict, repr=False)
    _tasks: set[asyncio.Task] = field(default_factory=set, repr=False)
    _train_cases: dict[str, list[Case]] = field(default_factory=dict, repr=False)

    # -- §9 verify ------------------------------------------------------
    async def verify(self, candidate: CandidateRef, profile: str | None = None) -> Verdict:
        name = profile or self.default_profile
        key = (candidate.candidate_id, name)
        existing = self._verdicts.get(key)
        if existing is not None:
            return existing
        pending = Verdict(
            candidate=candidate.candidate_id,
            bot_owner_id=candidate.bot_owner_id,
            bot_id=candidate.bot_id,
            run_id=candidate.run_id,
            revision=candidate.revision,
            parent_revision=candidate.parent_revision,
            profile=name,
            status=VerdictStatus.PENDING,
        )
        self._verdicts[key] = pending
        task = asyncio.create_task(self._run_pipeline(candidate, name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return pending

    async def get_verdict(self, candidate_id: str, profile: str | None = None) -> Verdict:
        return self._verdicts[(candidate_id, profile or self.default_profile)]

    async def strategy_view(self, candidate_id: str, run_id: str) -> StrategyVerdictView:
        for (cid, _profile), verdict in self._verdicts.items():
            if cid != candidate_id:
                continue
            if verdict.run_id is not None and verdict.run_id != run_id:
                raise PermissionError("candidate was not submitted by this run")
            aggregates: dict[str, AggregateScores] = {}
            validation = verdict.splits.get(Split.VALIDATION)
            if validation is not None and validation.ci is not None and validation.mean_delta is not None:
                aggregates["validation"] = AggregateScores(
                    cases=validation.cases,
                    seeds=validation.seeds,
                    mean_delta=validation.mean_delta,
                    ci=validation.ci,
                )
            return StrategyVerdictView(
                candidate=candidate_id,
                status=verdict.status,
                revision=verdict.revision,
                aggregates=aggregates,
                reasons=verdict.reasons_categories,
            )
        raise KeyError(candidate_id)

    async def settle(self, candidate_id: str, profile: str | None = None) -> Verdict:
        """测试与运维用：等待后台流水线结束。"""

        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        return await self.get_verdict(candidate_id, profile)

    # -- §4 pipeline ----------------------------------------------------
    async def _run_pipeline(self, candidate: CandidateRef, profile_name: str) -> None:
        profile = self.profiles[profile_name]
        key = (candidate.candidate_id, profile_name)
        try:
            verdict = await self._evaluate(candidate, profile)
        except Exception as exc:  # noqa: BLE001 - 执行失败是 inconclusive，不是异常
            current = self._verdicts[key]
            self._verdicts[key] = replace(
                current,
                status=VerdictStatus.INCONCLUSIVE,
                reasons=(f"evaluation failed: {exc}",),
                reasons_categories=(VerdictReasonCategory.VALIDATION_INCONCLUSIVE,),
                decided_at=_now(),
            )
            return
        self._verdicts[key] = verdict

    async def _evaluate(self, candidate: CandidateRef, profile: VerificationProfile) -> Verdict:
        bot = _bot(candidate)
        cases = list(await self.cases_for(bot, profile.splits))
        sanity_case = next((c for c in cases if c.category.value == "sanity"), None)
        sanity = SanityResult.NOT_RUN
        if sanity_case is not None:
            records = await self._paired(candidate, sanity_case, seeds=1)
            candidate_grade = records[0].grade
            sanity = (
                SanityResult.PASS
                if (candidate_grade and candidate_grade.score >= sanity_case.grading.pass_threshold)
                else SanityResult.FAIL
            )

        seeds = profile.seeds.default
        splits, rollouts, per_seed = await self._run_splits(candidate, cases, profile, seeds)
        while needs_more_seeds(splits, profile, seeds):
            seeds = min(profile.seeds.max, seeds + 1)
            splits, rollouts, per_seed = await self._run_splits(candidate, cases, profile, seeds)

        agreement = await self.agreement(rollouts) if self.agreement is not None else None
        status, categories, tolerance_used = decide(_floor_ref(), sanity, splits, agreement, profile)
        overfit = assess_overfit(splits, profile, per_seed)
        return Verdict(
            candidate=candidate.candidate_id,
            bot_owner_id=candidate.bot_owner_id,
            bot_id=candidate.bot_id,
            run_id=candidate.run_id,
            revision=candidate.revision,
            parent_revision=candidate.parent_revision,
            profile=profile.name,
            status=status,
            sanity=sanity,
            splits=splits,
            judge_agreement=agreement,
            overfit_flags=overfit.flags,
            tolerance_used=tolerance_used,
            cost=_cost_comparison(rollouts),
            verifier={"protocol": "bot-verification@1", "profile": profile.name},
            reasons=overfit.reasons,
            reasons_categories=categories,
            decided_at=_now(),
        )

    async def _run_splits(
        self,
        candidate: CandidateRef,
        cases: Sequence[Case],
        profile: VerificationProfile,
        seeds: int,
    ) -> tuple[dict[Split, SplitResult], list[RolloutRecord], dict[Split, list[float]]]:
        splits: dict[Split, SplitResult] = {}
        all_rollouts: list[RolloutRecord] = []
        per_seed: dict[Split, list[float]] = {}
        for split in profile.splits:
            split_cases = [c for c in cases if c.split is split]
            if not split_cases:
                continue
            subject_records: list[RolloutRecord] = []
            baseline_records: list[RolloutRecord] = []
            seed_means: list[float] = []
            for seed in range(1, seeds + 1):
                deltas: list[float] = []
                for case in split_cases:
                    records = await self._paired(candidate, case, seeds=1, seed=seed)
                    subject_records.append(records[0])
                    baseline_records.append(records[1])
                    if records[0].grade and records[1].grade:
                        deltas.append(records[0].grade.score - records[1].grade.score)
                if deltas:
                    seed_means.append(mean(deltas))
            threshold = split_cases[0].grading.pass_threshold
            splits[split] = compare_split(
                split,
                subject_records,
                baseline_records,
                confidence=profile.validation.confidence,
                pass_threshold=threshold,
            )
            per_seed[split] = seed_means
            all_rollouts.extend(subject_records)
            all_rollouts.extend(baseline_records)
        return splits, all_rollouts, per_seed

    async def _paired(
        self, candidate: CandidateRef, case: Case, *, seeds: int = 1, seed: int = 1
    ) -> tuple[RolloutRecord, RolloutRecord]:
        bot = _bot(candidate)
        subject = await self.execute(bot, candidate.revision, case, seed)
        baseline = await self.execute(bot, candidate.parent_revision, case, seed)
        subject = replace(subject, grade=await self.grade(case, subject))
        baseline = replace(baseline, grade=await self.grade(case, baseline))
        return subject, baseline

    # -- §5 train split -------------------------------------------------
    async def start_train_evaluation(
        self,
        run_id: str,
        workspace_id: str,
        *,
        idempotency_key: str,
        cases: list[str] | None = None,
        bot: Any = None,
    ) -> str:
        evaluation_id = "ev_" + secrets.token_hex(4)
        if bot is not None:
            case_objects = list(await self.cases_for(bot, (Split.TRAIN,)))
            if cases:
                case_objects = [c for c in case_objects if c.case_id in set(cases)]
            self._train_cases[evaluation_id] = case_objects
        self._evaluations[evaluation_id] = Evaluation(
            evaluation_id=evaluation_id,
            purpose="train",
            subject=workspace_id,
            baseline=None,
            status="queued",
            run_id=run_id,
        )
        return evaluation_id

    async def train_case_ids(self, evaluation_id: str) -> list[str]:
        return [case.case_id for case in self._train_cases.get(evaluation_id, [])]

    async def add_train_cases(self, run_id: str, cases: list[Case]) -> list[str]:
        for case in cases:
            if case.split is not Split.TRAIN:
                raise ValueError("strategy-authored cases always land in the train split")
        self._train_cases.setdefault(run_id, []).extend(cases)
        return [case.case_id for case in cases]

    # -- §6 holdout -----------------------------------------------------
    async def audit_holdout(
        self,
        bot: Any,
        revision_id: str,
        *,
        reason: HoldoutAuditReason,
        idempotency_key: str,
        parent_revision: str | None = None,
    ) -> str:
        evaluation_id = "ev_" + secrets.token_hex(4)
        self._evaluations[evaluation_id] = Evaluation(
            evaluation_id=evaluation_id,
            purpose=reason.value,
            subject=revision_id,
            baseline=parent_revision,
            status="queued",
        )
        return evaluation_id

    async def get_evaluation(
        self, bot: Any, evaluation_id: str, view: CallerView = CallerView.OPERATOR
    ) -> Evaluation:
        evaluation = self._evaluations[evaluation_id]
        if view is CallerView.STRATEGY:
            return replace(evaluation, rollouts=())
        return evaluation


def _bot(candidate: CandidateRef) -> Any:
    from avernet_genome.models import BotRef

    return BotRef(
        entity_id=candidate.bot_id,
        bot_id=candidate.bot_id,
        owner_id=candidate.bot_owner_id,
        engine_type="openclaw",
        bot_type="personal",
    )


def _floor_ref():
    from .models import FloorRef

    return FloorRef(passed=True)


def _cost_comparison(rollouts: Sequence[RolloutRecord]) -> CostComparison:
    def per_case(records: Sequence[RolloutRecord], revision: str) -> float:
        selected = [r for r in records if r.revision_id == revision and r.cost is not None]
        if not selected:
            return 0.0
        return mean(r.cost.usd for r in selected)

    revisions = {r.revision_id for r in rollouts}
    if len(revisions) < 2:
        return CostComparison()
    revisions = sorted(revisions)
    parent, candidate = revisions[0], revisions[1]
    parent_cost = per_case(rollouts, parent)
    candidate_cost = per_case(rollouts, candidate)
    increase = ((candidate_cost - parent_cost) / parent_cost * 100.0) if parent_cost else 0.0
    return CostComparison(
        parent_usd_per_case=parent_cost,
        candidate_usd_per_case=candidate_cost,
        increase_pct=increase,
        wins_by_spending=increase > 20.0,
    )


def _now() -> datetime:
    return datetime.now(UTC)
