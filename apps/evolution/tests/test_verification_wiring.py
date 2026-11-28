"""07→08→09 在组合根上的接线：判定落定 → 门禁决策 → 评审条目。

用平台自带的规则验证器驱动真实的 `compose()`，验证的是接线（判定持久化、
Ledger 回写、门禁回调、评审开单），不是评测质量。
"""

from __future__ import annotations

import pytest

from avernet_evolution.composition import compose
from avernet_evolution.core.evolution_promote import GateOutcome, RiskTier
from avernet_evolution.core.evolution_run.budget import BudgetUsage
from avernet_evolution.core.evolution_verify import Split, VerdictStatus
from avernet_evolution.local import BOT, build_local_platform
from avernet_evolution.plugins.verifiers import build_rule_verifier


def _binding(ceiling: str | None) -> dict:
    return {
        "id": "identity",
        "strategy": "local/identity-hardening@1.0.0",
        "trigger": {"manual": True},
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "rules-file@1",
        "budget": {"max_usd": 0.1, "max_wall_clock_s": 60},
        "params": {},
        "auto_promote_ceiling": ceiling,
    }


async def _run_once(settings, ceiling: str | None, *, edit_binding: bool = False, before_tick=None):
    platform = await build_local_platform(settings)
    verifier = build_rule_verifier(platform.genome)
    platform.verification = verifier
    runtime = await compose(settings, platform)
    bindings = [_binding(ceiling)]
    if edit_binding:
        bindings.append({**_binding("T2"), "id": "another-binding"})
    saved = await runtime.api.put_bindings(BOT, bindings, expected_etag=None, actor=BOT.owner_id)
    run_id = (
        await runtime.api.submit_run(
            BOT, {"binding_id": "identity", "idempotency_key": f"k-{ceiling}"}, actor=BOT.owner_id
        )
    )["run"]
    if edit_binding:
        updated_ceiling = "T2" if ceiling is None else None
        await runtime.api.put_bindings(
            BOT, [_binding(updated_ceiling), bindings[1]], expected_etag=saved["etag"], actor=BOT.owner_id
        )
    if before_tick is not None:
        before_tick(runtime)
    assert await runtime.runner.tick() == 1
    candidates = (await runtime.api.run_candidates(BOT, run_id))["candidates"]
    assert len(candidates) == 1
    candidate_id = candidates[0]["candidate_id"]
    verdict = await verifier.settle(candidate_id, "rules-file@1", run_id=run_id)

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
    runtime, _verifier, run_id, candidate_id, verdict = await _run_once(settings, "T2")
    try:
        assert verdict.status is VerdictStatus.ACCEPT
        decision = runtime.api.promotion.store.get(run_id, candidate_id)
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
        await runtime.ledger.record_submission(
            bot=BOT,
            run_id=run_id,
            binding_id="identity",
            iteration=1,
            candidate_id=candidate_id,
            mechanism="local/identity-hardening@1.0.0",
            parent_revision=parent,
            candidate_revision=parent,
            evidence=[],
            patch={},
            cost=BudgetUsage(),
            actor="local/identity-hardening@1.0.0",
        )
        # 模拟"进程在判定算完之前被杀"：库里只有 pending。
        VerdictStore(runtime.store.conn).save(
            Verdict(
                candidate=candidate_id,
                bot_owner_id=BOT.owner_id,
                bot_id=BOT.bot_id,
                run_id=run_id,
                revision=parent,
                parent_revision=parent,
                profile="rules-file@1",
                status=VerdictStatus.PENDING,
            )
        )
    finally:
        await runtime.close()

    platform = await build_local_platform(settings)
    verifier = build_rule_verifier(platform.genome)
    platform.verification = verifier
    runtime = await compose(settings, platform)
    try:
        verdict = await verifier.settle(candidate_id, "rules-file@1", run_id=run_id)
        # 候选与父版本相同 → 没有提升；关键是它落定了，而且不是因为取不到内容。
        assert verdict.status is VerdictStatus.REJECT, verdict.reasons
        assert not any("not the local fixture Bot" in reason for reason in verdict.reasons)
        assert await runtime.runner.tick() == 1  # 队列里仍然只有一个作业
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ceiling", "expected"), [(None, GateOutcome.NEEDS_REVIEW), ("T2", GateOutcome.AUTO_PROMOTE)]
)
async def test_gate_uses_the_submitted_binding_snapshot_not_current_bot_bindings(settings, ceiling, expected):
    runtime, _, run_id, candidate, verdict = await _run_once(settings, ceiling, edit_binding=True)
    try:
        assert verdict.status is VerdictStatus.ACCEPT
        assert runtime.store.get_run(run_id).auto_promote_ceiling == ceiling
        assert runtime.api.promotion.store.get(run_id, candidate).outcome is expected
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_restart_replays_final_effects_without_regrading_or_duplicate_evidence(settings, monkeypatch):
    def fail_gate_event(runtime):
        append = runtime.store.events.append

        def append_with_failure(kind, **kwargs):
            if kind == "gate.decided":
                raise RuntimeError("injected failure after gate and review commit")
            return append(kind, **kwargs)

        monkeypatch.setattr(runtime.store.events, "append", append_with_failure)

    runtime, verifier, run_id, candidate, first = await _run_once(settings, "T1", before_tick=fail_gate_event)
    try:
        assert first.status is VerdictStatus.ACCEPT
        assert verifier.store.undelivered() == [first]
        assert (await runtime.api.review_queue(BOT))["total"] == 1
    finally:
        await runtime.close()

    platform = await build_local_platform(settings)
    verifier = build_rule_verifier(platform.genome)
    platform.verification = verifier

    async def no_evaluation(*args):
        pytest.fail("a terminal verdict must not be re-evaluated")

    verifier.cases_for = no_evaluation
    runtime = await compose(settings, platform)
    try:
        assert await verifier.get_verdict(candidate, run_id=run_id) == first
        assert not verifier.store.undelivered()
        assert (await runtime.api.review_queue(BOT))["total"] == 1
        await runtime.runner.tick()
        kinds = [e["kind"] for e in runtime.store.events.for_run(run_id)]
        assert kinds.count("verdict.finalized") == kinds.count("gate.decided") == 1
        assert (
            runtime.ledger.conn.execute(
                "SELECT COUNT(*) FROM ledger_events WHERE type='verified'"
            ).fetchone()[0]
            == 1
        )
        assert (await runtime.api.run_candidates(BOT, run_id))["candidates"][0]["verdict"] == "accept"
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ceiling", ["T1", "T2"])
async def test_legacy_db_preserves_proven_reviews_but_replaces_unsafe_auto_decisions(settings, ceiling):
    from avernet_evolution.core.evolution_run.errors import IdempotencyKeyReused

    runtime, _, run_id, candidate, first = await _run_once(settings, ceiling)
    try:
        if ceiling == "T1":
            await runtime.api.approve_candidate(BOT, candidate, reason="old reason", actor="legacy-reviewer")
        with runtime.store.conn as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table, columns in (
                ("evolution_verdict", "candidate_id, profile, run_id, status, body, updated_at"),
                ("evolution_gate_decision", "*"),
                ("evolution_review_item", "*"),
            ):
                conn.execute(f"CREATE TEMP TABLE fixture AS SELECT {columns} FROM {table}")
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"CREATE TABLE {table} AS SELECT * FROM fixture")
                conn.execute("DROP TABLE fixture")
            # Legacy Run never froze a ceiling, and the old reason could not be recovered byte-for-byte.
            conn.execute("ALTER TABLE evolution_run DROP COLUMN auto_promote_ceiling")
            conn.execute(
                "CREATE TABLE evolution_review_idempotency (key TEXT PRIMARY KEY, body TEXT, created_at TEXT)"
            )
            conn.execute(
                "INSERT INTO evolution_review_idempotency VALUES (?, ?, '')",
                (f"{BOT.owner_id}:{BOT.bot_id}:{candidate}:approve:old-key", '{"legacy":"untrusted"}'),
            )
            conn.execute(
                "INSERT INTO evolution_run_candidate (run_id,candidate_id,revision,seq) VALUES (?, ?, ?, 2)",
                (run_id, "sha256:foreign", first.revision),
            )
            # SQL keys and candidate row match, but the JSON is copied from another candidate.
            conn.execute(
                "INSERT INTO evolution_verdict SELECT 'sha256:foreign', profile, run_id, status, body, updated_at "
                "FROM evolution_verdict WHERE candidate_id=?",
                (candidate,),
            )
    finally:
        await runtime.close()

    platform = await build_local_platform(settings)
    verifier = build_rule_verifier(platform.genome)
    platform.verification = verifier
    runtime = await compose(settings, platform)
    try:
        assert runtime.store.get_run(run_id).auto_promote_ceiling is None
        assert await verifier.get_verdict(candidate, run_id=run_id) == first
        assert runtime.store.conn.execute("SELECT COUNT(*) FROM evolution_verdict_legacy").fetchone()[0] == 2
        with pytest.raises(KeyError):
            await verifier.get_verdict("sha256:foreign", run_id=run_id)
        assert (await runtime.api.candidate_report(BOT, "sha256:foreign"))["verdict"] == "pending"
        report = await runtime.api.candidate_report(BOT, candidate)
        assert report["gate"]["outcome"] == "needs_review"
        assert report["review"]["status"] == ("approved" if ceiling == "T1" else "open")
        assert report["review"]["decided_by"] == ("legacy-reviewer" if ceiling == "T1" else None)
        gates = [
            e["details"]["gate_outcome"]
            for e in runtime.store.events.for_run(run_id)
            if e["kind"] == "gate.decided"
        ]
        assert gates == (["auto_promote", "needs_review"] if ceiling == "T2" else ["needs_review"])
        with pytest.raises(IdempotencyKeyReused, match="across the review upgrade"):
            await runtime.api.approve_candidate(
                BOT, candidate, reason="old reason", actor="reviewer", idempotency_key="old-key"
            )
    finally:
        await runtime.close()
