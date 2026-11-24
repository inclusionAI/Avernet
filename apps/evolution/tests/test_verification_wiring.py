"""07→08→09 在组合根上的接线：判定落定 → 门禁决策 → 评审条目。

这里用一个与 simulator 规则验证器同构的最小 verifier 驱动真实的
`compose()`，验证的是接线（判定持久化、Ledger 回写、门禁回调、评审开单），
不是评测质量。
"""

from __future__ import annotations

import pytest

from avernet_evolution.composition import compose
from avernet_evolution.core.evolution_promote import GateOutcome, RiskTier
from avernet_evolution.core.evolution_verify import (
    Case,
    CaseCategory,
    CaseOrigin,
    CaseOriginKind,
    ExecutorKind,
    Grade,
    JudgingSpec,
    LocalVerificationService,
    RolloutRecord,
    Split,
    VerdictStatus,
    VerificationProfile,
)
from avernet_evolution.core.evolution_verify.models import SeedPolicy, ValidationRule
from avernet_evolution.local import BOT, build_local_platform

MARKERS = ("先读后改，不动猜测。", "引用证据，不凭空断言。", "最小改动，先测再提。")


def _cases() -> tuple[Case, ...]:
    def case(case_id: str, split: Split, rubric: str) -> Case:
        return Case(
            case_id=case_id,
            suite_id="tests/rules",
            suite_version=1,
            split=split,
            digest=f"sha256:{case_id}",
            origin=CaseOrigin(kind=CaseOriginKind.AUTHORED),
            name=case_id,
            category=CaseCategory.SAFETY if split is Split.SAFETY else CaseCategory.ESCALATION,
            grading=JudgingSpec(grader="tests/rules@1", pass_threshold=0.5, rubric=rubric),
        )

    return (
        *(case(f"validation-{i}", Split.VALIDATION, marker) for i, marker in enumerate(MARKERS, 1)),
        case("regression-heading", Split.REGRESSION, "## Core Truths"),
        case("safety-no-secret", Split.SAFETY, ""),
    )


def _verifier(genome) -> LocalVerificationService:
    cases = _cases()
    texts: dict[tuple[str, str, int], str] = {}

    async def cases_for(bot, splits):
        del bot
        return [item for item in cases if item.split in splits]

    async def execute(bot, revision_id, case, seed):
        revision = await genome.resolve_revision(bot, revision_id)
        files = await genome.snapshot_files(revision, bot=bot)
        text = "\n".join(value.decode("utf-8", "replace") for value in files.values())
        texts[(case.case_id, revision_id, seed)] = text
        return RolloutRecord(
            case_id=case.case_id, revision_id=revision_id, seed=seed, executor=ExecutorKind.LOCAL_SANDBOX
        )

    async def grade(case, rollout):
        text = texts.get((case.case_id, rollout.revision_id, rollout.seed), "")
        if case.category is CaseCategory.SAFETY:
            return Grade(score=0.0 if "sk-" in text else 1.0)
        marker = case.grading.rubric
        return Grade(score=1.0 if not marker or marker in text else 0.0)

    profile = VerificationProfile(
        name="pending@1",
        splits=(Split.VALIDATION, Split.REGRESSION, Split.SAFETY),
        seeds=SeedPolicy(default=1, escalate_within=0.0, max=1),
        validation=ValidationRule(min_effect=0.02, confidence=0.95),
        regression_tolerance=0,
    )
    return LocalVerificationService(
        profiles={"pending@1": profile},
        cases_for=cases_for,
        execute=execute,
        grade=grade,
        default_profile="pending@1",
    )


def _binding(ceiling: str | None) -> dict:
    return {
        "id": "identity",
        "strategy": "local/identity-hardening@1.0.0",
        "trigger": {"manual": True},
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "pending@1",
        "budget": {"max_usd": 0.1, "max_wall_clock_s": 60},
        "params": {},
        "auto_promote_ceiling": ceiling,
    }


async def _run_once(settings, ceiling: str | None):
    platform = await build_local_platform(settings)
    verifier = _verifier(platform.genome)
    platform.verification = verifier
    runtime = await compose(settings, platform)
    await runtime.api.put_bindings(BOT, [_binding(ceiling)], expected_etag=None, actor=BOT.owner_id)
    run_id = (
        await runtime.api.submit_run(
            BOT, {"binding_id": "identity", "idempotency_key": f"k-{ceiling}"}, actor=BOT.owner_id
        )
    )["run"]
    assert await runtime.runner.tick() == 1
    candidates = (await runtime.api.run_candidates(BOT, run_id))["candidates"]
    assert len(candidates) == 1
    candidate_id = candidates[0]["candidate_id"]
    verdict = await verifier.settle(candidate_id, "pending@1")

    return runtime, verifier, run_id, candidate_id, verdict


@pytest.mark.asyncio
async def test_persona_candidate_is_verified_and_sent_to_review(settings) -> None:
    runtime, _verifier, run_id, candidate_id, verdict = await _run_once(settings, "T1")
    try:
        # 07：规则验证真的给出 accept（候选补上了三条 core truths）。
        assert verdict.status is VerdictStatus.ACCEPT, verdict.reasons
        assert verdict.splits[Split.VALIDATION].mean_delta == 1.0

        # 判定落定后写回 Ledger 并留下证据。
        entry = runtime.ledger.conn.execute(
            "SELECT verdict FROM ledger_entries WHERE run_id=? AND candidate_id=?", (run_id, candidate_id)
        ).fetchone()
        assert entry is not None and entry["verdict"] is not None
        kinds = [event["kind"] for event in runtime.store.events.for_run(run_id)]
        assert "verdict.finalized" in kinds and "gate.decided" in kinds

        # 08：persona 是 T2，绑定上限 T1 → 需要人工评审，且真的开了单。
        queue = await runtime.api.review_queue(BOT)
        assert [item["candidate_id"] for item in queue["items"]] == [candidate_id]
        assert queue["items"][0]["risk_tier"] == RiskTier.T2.value

        report = await runtime.api.candidate_report(BOT, candidate_id)
        assert report["verdict"] == "accept"
        assert report["gate"]["outcome"] == GateOutcome.NEEDS_REVIEW.value
        assert report["review"]["status"] == "open"
        assert report["patch_summary"][0]["target"] == "persona/SOUL.md"

        # 09：批准记录决定，但 apply 仍然显式关闭。
        approved = await runtime.api.approve_candidate(
            BOT, candidate_id, reason="diff reviewed", actor=BOT.owner_id
        )
        assert approved["review"]["status"] == "approved"
        assert approved["promotion"] is None and approved["promotion_note"]
        assert (await runtime.api.review_queue(BOT))["total"] == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_ceiling_at_t2_auto_promotes_without_a_review_item(settings) -> None:
    runtime, _verifier, _run_id, candidate_id, verdict = await _run_once(settings, "T2")
    try:
        assert verdict.status is VerdictStatus.ACCEPT
        decision = runtime.api.promotion.store.get(candidate_id)
        assert decision is not None and decision.outcome is GateOutcome.AUTO_PROMOTE
        # 自动晋升不排队；active 不会被动过（apply 未启用）。
        assert (await runtime.api.review_queue(BOT))["total"] == 0
        before = await runtime.platform.genome.resolve_revision(BOT, "active")
        assert before.revision.id
        assert (
            runtime.platform.genome.conn.execute(
                "SELECT revision FROM fixture_refs WHERE name='active'"
            ).fetchone()[0]
            == before.revision.id
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_restart_resumes_a_pending_verdict_with_the_real_bot(settings) -> None:
    """进程内流水线不跨重启：重启后按 run 行还原 BotRef 再补跑。

    如果只按 id 重建 BotRef，`LocalGenome` 会以 "not the local fixture Bot" 拒绝，
    判定就会以 inconclusive 落定——这个断言正是用来挡住那个回归。
    """

    from avernet_evolution.core.evolution_verify import Verdict
    from avernet_evolution.core.evolution_verify.store import VerdictStore

    platform = await build_local_platform(settings)
    runtime = await compose(settings, platform)
    candidate_id = "sha256:restart"
    try:
        await runtime.api.put_bindings(BOT, [_binding("T1")], expected_etag=None, actor=BOT.owner_id)
        run_id = (
            await runtime.api.submit_run(
                BOT, {"binding_id": "identity", "idempotency_key": "restart"}, actor=BOT.owner_id
            )
        )["run"]
        parent = (await platform.genome.resolve_revision(BOT, "active")).revision.id
        runtime.store.upsert_candidate(run_id, candidate_id, parent, seq=1)
        # 模拟"进程在判定算完之前被杀"：库里只有 pending。
        VerdictStore(runtime.store.conn).save(
            Verdict(
                candidate=candidate_id,
                bot_owner_id=BOT.owner_id,
                bot_id=BOT.bot_id,
                run_id=run_id,
                revision=parent,
                parent_revision=parent,
                profile="pending@1",
                status=VerdictStatus.PENDING,
            )
        )
    finally:
        await runtime.close()

    platform = await build_local_platform(settings)
    verifier = _verifier(platform.genome)
    platform.verification = verifier
    runtime = await compose(settings, platform)
    try:
        verdict = await verifier.settle(candidate_id, "pending@1")
        # 候选与父版本相同 → 没有提升；关键是它落定了，而且不是因为取不到内容。
        assert verdict.status is VerdictStatus.REJECT, verdict.reasons
        assert not any("not the local fixture Bot" in reason for reason in verdict.reasons)
        assert await runtime.runner.tick() == 1  # 队列里仍然只有一个作业
    finally:
        await runtime.close()
