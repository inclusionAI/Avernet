"""验证流水线与判定规则（07）。"""

from __future__ import annotations

import pytest

from avernet_evolution.core.evolution_verify import (
    Case,
    CaseCategory,
    CaseOrigin,
    CaseOriginKind,
    Grade,
    LocalVerificationService,
    RolloutRecord,
    SanityResult,
    Split,
    VerdictStatus,
    VerificationProfile,
)


def _case(case_id: str, split: Split, category=CaseCategory.REFUNDS) -> Case:
    return Case(
        case_id=case_id,
        suite_id="support-core",
        suite_version=1,
        split=split,
        digest=f"sha256:{case_id}",
        origin=CaseOrigin(CaseOriginKind.AUTHORED),
        name=case_id,
        category=category,
    )


CASES = [
    _case("sanity_00", Split.VALIDATION, CaseCategory.SANITY),
    _case("val_01", Split.VALIDATION),
    _case("val_02", Split.VALIDATION),
    _case("reg_01", Split.REGRESSION),
    _case("saf_01", Split.SAFETY, CaseCategory.SAFETY),
]


class Harness:
    """执行器按修订版给分；父版本在必过用例上通过。"""

    def __init__(
        self,
        *,
        candidate: float = 0.9,
        parent: float = 0.7,
        agreement: float | None = None,
    ) -> None:
        self.scores = {"sha256:cand": candidate, "sha256:parent": parent}
        self.agreement_value = agreement

    async def cases_for(self, bot, splits):
        return [case for case in CASES if case.split in splits]

    async def execute(self, bot, revision, case, seed):
        return RolloutRecord(
            case_id=case.case_id,
            revision_id=revision,
            seed=seed,
            executor="local_sandbox",
            grade=None,
        )

    async def grade(self, case, rollout):
        base = self.scores.get(rollout.revision_id, 0.5)
        return Grade(score=min(1.0, base + 0.01 * (rollout.seed - 1)))

    async def agreement(self, rollouts):
        return self.agreement_value

    def service(self, **profile_overrides) -> LocalVerificationService:
        profile = VerificationProfile(**profile_overrides)
        return LocalVerificationService(
            profiles={"default@1": profile},
            cases_for=self.cases_for,
            execute=self.execute,
            grade=self.grade,
            agreement=self.agreement,
        )


async def _candidate(harness: Harness):
    service = harness.service()
    pending = await service.verify(_ref(), "default@1")
    assert pending.status is VerdictStatus.PENDING
    return service, await service.settle("sha256:cand")


def _ref():
    from avernet_evolution.core.evolution_verify import CandidateRef

    return CandidateRef(
        candidate_id="sha256:cand",
        bot_owner_id="user_5",
        bot_id="bot_123",
        revision="sha256:cand",
        parent_revision="sha256:parent",
        run_id="run_1",
    )


@pytest.mark.asyncio
async def test_improving_candidate_is_accepted() -> None:
    _service, verdict = await _candidate(Harness(candidate=0.9, parent=0.7))
    assert verdict.status is VerdictStatus.ACCEPT
    assert verdict.sanity is SanityResult.PASS
    validation = verdict.splits[Split.VALIDATION]
    assert validation.mean_delta and validation.mean_delta > 0.1
    assert verdict.decided_at is not None


@pytest.mark.asyncio
async def test_verify_is_idempotent_per_candidate() -> None:
    harness = Harness()
    service = harness.service()
    first = await service.verify(_ref(), "default@1")
    second = await service.verify(_ref(), "default@1")
    assert first.status is VerdictStatus.PENDING and second.status is VerdictStatus.PENDING
    await service.settle("sha256:cand")
    again = await service.verify(_ref(), "default@1")
    assert again.status is VerdictStatus.ACCEPT


@pytest.mark.asyncio
async def test_judge_disagreement_is_inconclusive() -> None:
    _service, verdict = await _candidate(Harness(candidate=0.9, parent=0.7, agreement=0.2))
    assert verdict.status is VerdictStatus.INCONCLUSIVE
    assert verdict.judge_agreement == 0.2


@pytest.mark.asyncio
async def test_no_gain_is_rejected() -> None:
    _service, verdict = await _candidate(Harness(candidate=0.7, parent=0.7))
    assert verdict.status is VerdictStatus.REJECT


@pytest.mark.asyncio
async def test_strategy_view_hides_case_detail() -> None:
    service, _verdict = await _candidate(Harness(candidate=0.9, parent=0.7))
    view = await service.strategy_view("sha256:cand", "run_1")
    assert set(view.aggregates) == {"validation"}
    assert set(view.aggregates["validation"].__dict__) == {
        "cases",
        "seeds",
        "mean_delta",
        "ci",
    }
    assert not hasattr(view, "splits")


@pytest.mark.asyncio
async def test_strategy_view_refuses_other_runs() -> None:
    service, _ = await _candidate(Harness())
    with pytest.raises(PermissionError):
        await service.strategy_view("sha256:cand", "run_other")


@pytest.mark.asyncio
async def test_train_cases_are_forced_into_train_split() -> None:
    service, _ = await _candidate(Harness())
    with pytest.raises(ValueError):
        await service.add_train_cases("run_1", [_case("x", Split.VALIDATION)])
    ids = await service.add_train_cases("run_1", [_case("x", Split.TRAIN)])
    assert ids == ["x"]
