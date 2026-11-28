"""09 §10.1：候选报告、评审队列与 approve/reject 的 HTTP 门面。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from avernet_evolution.core.evolution_ledger import LocalExperimentLedger
from avernet_evolution.core.evolution_promote import (
    AlreadyDecided,
    GateDecisionStore,
    LocalPromotionService,
    ReviewStatus,
    RiskTier,
)
from avernet_evolution.core.evolution_run import EvolutionApi
from avernet_evolution.core.evolution_verify import (
    LocalVerificationService,
    Split,
    SplitResult,
    Verdict,
    VerdictStatus,
)
from avernet_evolution.core.evolution_verify.store import VerdictStore

from .conftest import BOT

CANDIDATE = "sha256:c41e"
REVISION = "sha256:7c1e"


def _binding_doc() -> dict:
    return {
        "id": "bind_01",
        "strategy": "acme/identity-hardening@1.0.0",
        "trigger": {"manual": True},
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "default@1",
        "budget": {"max_usd": 5, "max_wall_clock_s": 3600},
        "params": {},
    }


async def _stack(
    services,
    *,
    risk: RiskTier = RiskTier.T2,
    with_verification: bool = True,
    bot=BOT,
):
    """一个已提交候选、已判定、已开门禁的最小运行时。"""

    api = EvolutionApi(bindings=services.bindings, runs=services.runs)
    await api.put_bindings(bot, [_binding_doc()], expected_etag=None, actor="u")
    run_id = (
        await api.submit_run(
            bot, {"binding_id": "bind_01", "idempotency_key": "k1", "trigger": {"manual": True}}, actor="u"
        )
    )["run"]

    ledger = LocalExperimentLedger(":memory:")
    entry_id = await ledger.record_submission(
        bot=bot,
        run_id=run_id,
        binding_id="bind_01",
        iteration=1,
        candidate_id=CANDIDATE,
        mechanism="acme/identity-hardening@1.0.0",
        parent_revision="sha256:a90b",
        candidate_revision=REVISION,
        evidence=["episode:ep_91"],
        patch={
            "ops": 1,
            "rationale": "escalation section missed refunds above the tier limit",
            "items": [{"op": "file.edit", "target": "persona/SOUL.md", "tier": "T2"}],
        },
        cost=SimpleNamespace(usd="0.02", tokens=10, rollouts=1),
        actor="acme/identity-hardening@1.0.0",
    )
    assert entry_id

    services.store.upsert_candidate(run_id, CANDIDATE, REVISION, seq=1)
    services.store.set_candidate_risk(CANDIDATE, risk.value)

    verdicts = VerdictStore(services.store.conn)
    verdicts.save(
        Verdict(
            candidate=CANDIDATE,
            bot_owner_id=bot.owner_id,
            bot_id=bot.bot_id,
            run_id=run_id,
            revision=REVISION,
            parent_revision="sha256:a90b",
            profile="default@1",
            status=VerdictStatus.ACCEPT,
            splits={
                Split.VALIDATION: SplitResult(cases=3, seeds=1, mean_delta=0.12, ci=(0.05, 0.19)),
                Split.REGRESSION: SplitResult(cases=2, seeds=1, mean_delta=0.0, ci=(0.0, 0.0)),
            },
        )
    )

    promotion = LocalPromotionService(verdicts=verdicts, store=GateDecisionStore(services.store.conn))
    verification = None
    if with_verification:
        # 只用到读路径：判定已经在库里，报告应当能读到。
        verification = LocalVerificationService(
            profiles={},
            cases_for=_never_cases,
            execute=_never_execute,
            grade=_never_grade,
            store=verdicts,
        )
    api = EvolutionApi(
        bindings=services.bindings,
        runs=services.runs,
        ledger=ledger,
        verification=verification,
        promotion=promotion,
        genome=services.genome,
    )
    return api, promotion, run_id


@pytest.mark.asyncio
async def test_report_joins_ledger_verdict_gate_and_review(services) -> None:
    api, promotion, run_id = await _stack(services)
    await promotion.decide(BOT, CANDIDATE, run_id=run_id, profile="default@1", ceiling=RiskTier.T1)

    report = await api.candidate_report(BOT, CANDIDATE)
    assert report["candidate_id"] == CANDIDATE
    assert report["bot"] == {"owner_id": BOT.owner_id, "bot_id": BOT.bot_id}
    assert report["revision_id"] == REVISION
    assert report["run_id"] == run_id
    assert report["strategy"] == "acme/identity-hardening"
    assert report["strategy_version"] == "1.0.0"
    assert report["rationale"].startswith("escalation section")
    assert report["evidence"] == ["episode:ep_91"]
    assert report["patch_summary"][0]["target"] == "persona/SOUL.md"
    assert report["diff_path"] == (
        f"/bots/{BOT.bot_id}/genome/revisions/{REVISION}/diff?against={report['parent_revision_id']}"
    )
    # 判定是 accept，门禁因为 T2 > T1 仍要人看。
    assert report["verdict"] == "accept"
    assert {item["split"] for item in report["splits"]} == {"validation", "regression"}
    assert report["gate"]["outcome"] == "needs_review"
    assert report["gate"]["risk"]["tier"] == "T2"
    assert report["review"]["status"] == "open"


@pytest.mark.asyncio
async def test_report_without_verification_stays_pending(services) -> None:
    api, _promotion, _run_id = await _stack(services, with_verification=False)
    report = await api.candidate_report(BOT, CANDIDATE)
    assert report["verdict"] == "pending"
    assert report["gate"] is None and report["review"] is None


@pytest.mark.asyncio
async def test_unknown_candidate_is_not_found(services) -> None:
    from avernet_evolution.core.evolution_promote import CandidateNotFound

    api, _promotion, _run_id = await _stack(services)
    with pytest.raises(CandidateNotFound):
        await api.candidate_report(BOT, "sha256:missing")


@pytest.mark.asyncio
async def test_queue_and_approve_replay_the_same_idempotency_key(services) -> None:
    api, promotion, run_id = await _stack(services)
    await promotion.decide(BOT, CANDIDATE, run_id=run_id, profile="default@1", ceiling=RiskTier.T1)

    queue = await api.review_queue(BOT)
    assert queue["total"] == 1
    item = queue["items"][0]
    assert item["candidate_id"] == CANDIDATE
    assert item["risk_tier"] == "T2"
    assert item["rationale"].startswith("escalation section")
    assert item["evidence"] == ["episode:ep_91"]

    first = await api.approve_candidate(
        BOT, CANDIDATE, reason="diff reviewed", actor="user_17", idempotency_key="review-1"
    )
    assert first["review"]["status"] == "approved"
    assert first["review"]["decided_by"] == "user_17"
    assert first["promotion"] is None and first["promotion_note"]

    # 同一幂等键重试：返回第一次的结果，不再做新操作。
    assert (
        await api.approve_candidate(
            BOT, CANDIDATE, reason="diff reviewed", actor="user_17", idempotency_key="review-1"
        )
        == first
    )
    # 不同键再批一次：已经是终态。
    with pytest.raises(AlreadyDecided):
        await api.approve_candidate(
            BOT, CANDIDATE, reason="again", actor="user_17", idempotency_key="review-2"
        )

    assert (await api.review_queue(BOT))["total"] == 0
    after = await api.review_queue(BOT, status="all")
    assert after["items"][0]["status"] == ReviewStatus.APPROVED.value


@pytest.mark.asyncio
async def test_reject_requires_a_reason_and_closes_the_item(services) -> None:
    api, promotion, run_id = await _stack(services)
    await promotion.decide(BOT, CANDIDATE, run_id=run_id, profile="default@1", ceiling=RiskTier.T1)

    with pytest.raises(ValueError):
        await api.reject_candidate(BOT, CANDIDATE, reason="", actor="user_17")

    result = await api.reject_candidate(
        BOT, CANDIDATE, reason="drops the legal-hold exception", actor="user_17"
    )
    assert result["review"]["status"] == "rejected"
    assert result["review"]["decision_reason"] == "drops the legal-hold exception"


@pytest.mark.asyncio
async def test_queue_rejects_an_unknown_status_filter(services) -> None:
    api, _promotion, _run_id = await _stack(services)
    with pytest.raises(ValueError):
        await api.review_queue(BOT, status="whatever")


async def _never_cases(bot, splits):
    del bot, splits
    return []


async def _never_execute(bot, revision, case, seed):
    del bot, revision, case, seed
    raise AssertionError("read-only verification stub must not execute")


async def _never_grade(case, rollout):
    del case, rollout
    raise AssertionError("read-only verification stub must not grade")


@pytest.mark.asyncio
async def test_same_hash_on_two_bots_never_shares_review_or_key(services) -> None:
    other = replace(BOT, bot_id="other-bot", entity_id="other-entity")
    api, promotion, first_run = await _stack(services)
    other_api, _, second_run = await _stack(services, bot=other)
    for bot, run_id in ((BOT, first_run), (other, second_run)):
        await promotion.decide(bot, CANDIDATE, run_id=run_id, profile="default@1")
    await api.approve_candidate(BOT, CANDIDATE, reason="ok", actor="reviewer-a", idempotency_key="shared")
    await other_api.reject_candidate(
        other, CANDIDATE, reason="no", actor="reviewer-b", idempotency_key="shared"
    )
    a = await api.candidate_report(BOT, CANDIDATE)
    b = await other_api.candidate_report(other, CANDIDATE)
    assert (a["run_id"], a["review"]["status"], a["review"]["decided_by"]) == (
        first_run,
        "approved",
        "reviewer-a",
    )
    assert (b["run_id"], b["review"]["status"], b["review"]["decided_by"]) == (
        second_run,
        "rejected",
        "reviewer-b",
    )
    assert a["gate"]["id"] != b["gate"]["id"]


@pytest.mark.asyncio
async def test_same_bot_hash_only_route_refuses_ambiguous_history(services) -> None:
    from avernet_evolution.core.evolution_run.errors import CandidateAmbiguous

    api, promotion, run_id = await _stack(services)
    await promotion.decide(BOT, CANDIDATE, run_id=run_id, profile="default@1")
    # Synthesize a second Run (the Registry normally refuses a cross-Run collision).
    run = services.store.get_run(run_id)
    assert run is not None
    second_id = "another-recorded-run"
    services.store.insert_run(
        replace(run, id=second_id, idempotency_key="k2"), BOT.owner_id, BOT.bot_id, "other"
    )
    services.store.upsert_candidate(second_id, CANDIDATE, REVISION, seq=1)
    with pytest.raises(CandidateAmbiguous):
        await api.candidate_report(BOT, CANDIDATE)
    with pytest.raises(CandidateAmbiguous):
        await api.reject_candidate(BOT, CANDIDATE, reason="no", actor="reviewer")
    assert promotion.store.get_review(run_id, CANDIDATE).status is ReviewStatus.OPEN


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["remember", "event"])
async def test_review_write_failure_rolls_back_decision_audit_and_key(
    services, monkeypatch, failure_point
) -> None:
    api, promotion, run_id = await _stack(services)
    await promotion.decide(BOT, CANDIDATE, run_id=run_id, profile="default@1")

    def fail(*args, **kwargs):
        raise RuntimeError("injected write failure")

    with monkeypatch.context() as patch:
        if failure_point == "remember":
            patch.setattr(promotion.store, "remember", fail)
        else:
            patch.setattr(services.store.events, "write_review_decided", fail)
        with pytest.raises(RuntimeError, match="injected write failure"):
            await api.approve_candidate(BOT, CANDIDATE, reason="ok", actor="reviewer", idempotency_key="k")
    assert promotion.store.get_review(run_id, CANDIDATE).status is ReviewStatus.OPEN
    assert promotion.store.replay(BOT.owner_id, BOT.bot_id, "k") is None
    assert not any(e["kind"] == "review.decided" for e in services.store.events.for_run(run_id))
    result = await api.approve_candidate(BOT, CANDIDATE, reason="ok", actor="reviewer", idempotency_key="k")
    assert result["review"]["status"] == "approved"
    assert [e["kind"] for e in services.store.events.for_run(run_id)].count("review.decided") == 1
