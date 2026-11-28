"""07：判定必须跨重启可读，且 verify 的幂等性不依赖进程内存。"""

from __future__ import annotations

import pytest

from avernet_evolution.core.evolution_run.store import EvolutionStore
from avernet_evolution.core.evolution_verify import (
    CandidateRef,
    Case,
    CaseOrigin,
    CaseOriginKind,
    ExecutorKind,
    Grade,
    JudgingSpec,
    LocalVerificationService,
    RolloutRecord,
    SeedPolicy,
    Split,
    ValidationRule,
    VerdictStatus,
    VerificationProfile,
)
from avernet_evolution.core.evolution_verify.codec import verdict_from_json, verdict_to_json

CASES = tuple(
    Case(
        case_id=f"case-{index}",
        suite_id="suite-validation",
        suite_version=1,
        split=Split.VALIDATION,
        digest=f"digest-{index}",
        origin=CaseOrigin(kind=CaseOriginKind.AUTHORED),
        name=f"case {index}",
        grading=JudgingSpec(grader="rule-based", pass_threshold=0.5),
    )
    for index in range(3)
)

PROFILE = VerificationProfile(
    name="default@1",
    splits=(Split.VALIDATION,),
    seeds=SeedPolicy(default=1, escalate_within=0.0, max=1),
    validation=ValidationRule(min_effect=0.1, confidence=0.95),
    regression_tolerance=0,
)

CANDIDATE = CandidateRef(
    candidate_id="sha256:candidate",
    bot_owner_id="owner-1",
    bot_id="bot-1",
    revision="sha256:new",
    parent_revision="sha256:old",
    run_id="run_1",
)


async def _cases_for(bot, splits):
    return [case for case in CASES if case.split in splits]


async def _execute(bot, revision, case, seed):
    return RolloutRecord(
        case_id=case.case_id, revision_id=revision, seed=seed, executor=ExecutorKind.LOCAL_SANDBOX
    )


async def _grade(case, rollout):
    return Grade(score=0.9 if rollout.revision_id == "sha256:new" else 0.4)


def _service(**kwargs) -> LocalVerificationService:
    return LocalVerificationService(
        profiles={"default@1": PROFILE},
        cases_for=_cases_for,
        execute=_execute,
        grade=_grade,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_verdict_survives_a_new_service_instance() -> None:
    store = EvolutionStore(":memory:")
    finalized: list[str] = []

    async def on_final(verdict) -> None:
        finalized.append(verdict.status.value)

    first = _service(on_final=on_final)
    first.attach_store(store)
    assert (await first.verify(CANDIDATE, "default@1")).status is VerdictStatus.PENDING
    verdict = await first.settle(CANDIDATE.candidate_id, "default@1", run_id=CANDIDATE.run_id)
    assert verdict.status is VerdictStatus.ACCEPT
    assert finalized == ["accept"]

    # 新实例共用同一个库：不再重新评测，直接读到已有判定。
    second = _service()
    second.attach_store(store)
    reloaded = await second.get_verdict(CANDIDATE.candidate_id, "default@1", run_id=CANDIDATE.run_id)
    assert reloaded.status is VerdictStatus.ACCEPT
    assert reloaded.revision == "sha256:new"
    assert (await second.verify(CANDIDATE, "default@1")).status is VerdictStatus.ACCEPT


@pytest.mark.asyncio
async def test_pending_verdict_is_visible_while_the_pipeline_runs() -> None:
    store = EvolutionStore(":memory:")
    service = _service()
    service.attach_store(store)
    await service.verify(CANDIDATE, "default@1")
    # 落盘先于流水线：崩溃后运维仍能看到"这个候选在验证中"。
    assert store.conn.execute("SELECT status FROM evolution_verdict").fetchone()["status"] == "pending"
    await service.settle(CANDIDATE.candidate_id, "default@1", run_id=CANDIDATE.run_id)


def test_verdict_codec_round_trips_the_decision_fields() -> None:
    from avernet_evolution.core.evolution_verify import Verdict

    verdict = Verdict(
        candidate="sha256:candidate",
        bot_owner_id="owner-1",
        bot_id="bot-1",
        run_id="run_1",
        revision="sha256:new",
        parent_revision="sha256:old",
        profile="default@1",
        status=VerdictStatus.ACCEPT,
        judge_agreement=0.9,
        tolerance_used=True,
    )
    restored = verdict_from_json(verdict_to_json(verdict))
    assert restored.status is VerdictStatus.ACCEPT
    assert restored.revision == "sha256:new"
    assert restored.judge_agreement == 0.9
    assert restored.tolerance_used is True


@pytest.mark.asyncio
async def test_same_candidate_hash_is_evaluated_independently_and_terminal_is_immutable() -> None:
    from dataclasses import replace

    store = EvolutionStore(":memory:")
    service = _service()
    service.attach_store(store)
    other = replace(CANDIDATE, run_id="run_2", bot_id="bot-2", revision=CANDIDATE.parent_revision)
    await service.verify(CANDIDATE)
    await service.verify(other)
    first = await service.settle(CANDIDATE.candidate_id, run_id=CANDIDATE.run_id)
    second = await service.settle(other.candidate_id, run_id=other.run_id)
    assert first.status is VerdictStatus.ACCEPT
    assert second.status is VerdictStatus.REJECT
    service.store.save(replace(first, status=VerdictStatus.PENDING))
    assert await service.get_verdict(first.candidate, run_id=first.run_id) == first
    assert (await service.strategy_view(second.candidate, second.run_id)).status is VerdictStatus.REJECT
    with pytest.raises(KeyError):
        await service.strategy_view(first.candidate, "foreign")
