"""组合根：调度 → 执行 → 候选 → 验证 → 门禁 的端到端装配。"""

from __future__ import annotations

from dataclasses import replace

import pytest
from avernet_evolution.composition import compose
from avernet_evolution.config import Settings
from avernet_evolution.core.evolution_promote import GateOutcome, PromotionNotReady
from avernet_evolution.core.evolution_run import (
    Binding,
    Budget,
    ContainerHost,
    EventName,
    EventTrigger,
    RunStatus,
)
from avernet_evolution.core.evolution_verify import (
    Case,
    CaseCategory,
    CaseOrigin,
    CaseOriginKind,
    Grade,
    LocalVerificationService,
    RolloutRecord,
    Split,
    VerificationProfile,
)
from avernet_evolution.plugin_api import Platform
from tests.community.factories.evolution_runtime import (
    BOT,
    FakeGenome,
    FakeGovernance,
    FakeKills,
    FakeRegistry,
    registration,
)

CASES = [
    Case(
        case_id="sanity",
        suite_id="s",
        suite_version=1,
        split=Split.VALIDATION,
        digest="d1",
        origin=CaseOrigin(CaseOriginKind.AUTHORED),
        name="sanity",
        category=CaseCategory.SANITY,
    ),
    Case(
        case_id="v1",
        suite_id="s",
        suite_version=1,
        split=Split.VALIDATION,
        digest="d2",
        origin=CaseOrigin(CaseOriginKind.AUTHORED),
        name="v1",
    ),
    Case(
        case_id="r1",
        suite_id="s",
        suite_version=1,
        split=Split.REGRESSION,
        digest="d3",
        origin=CaseOrigin(CaseOriginKind.AUTHORED),
        name="r1",
    ),
    Case(
        case_id="x1",
        suite_id="s",
        suite_version=1,
        split=Split.SAFETY,
        digest="d4",
        origin=CaseOrigin(CaseOriginKind.AUTHORED),
        name="x1",
        category=CaseCategory.SAFETY,
    ),
]

SCORES = {"sha256:parent": 0.7, "sha256:new": 0.95}


class Strategy:
    async def run(self, ctx):
        await ctx.experience.sessions(days=7, limit=5)
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/r1")
        await ws.write("persona/SOUL.md", b"## Truths\n- plain language\n")
        from avernet_evolution.core.evolution_strategy import Candidate, RunSummary

        patch = await ws.to_patch(rationale="harden")
        await ctx.candidates.submit(
            Candidate(
                patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence)
            )
        )
        return RunSummary(summary="done", metrics={"candidates": 1.0})


class Experience:
    async def sessions(self, *, days, limit=500, revision=None):
        return []

    async def feedback(self, *, days, kinds=None, limit=500):
        return []


async def cases_for(bot, splits):
    return [c for c in CASES if c.split in splits]


async def execute(bot, revision, case, seed):
    return RolloutRecord(
        case_id=case.case_id, revision_id=revision, seed=seed, executor="local_sandbox"
    )


async def grade(case, rollout):
    return Grade(score=SCORES.get(rollout.revision_id, 0.5))


async def _compose(
    tmp_path, *, genome, registry, governance, kills, strategies, sandbox=None, image=""
):
    """Exercise the production composition, never a second acceptance orchestrator."""
    from avernet_evolution.core.evolution_strategy import Runtime, RuntimeKind

    key = tmp_path / "worker-key"
    key.write_bytes(b"test-signing-key-for-canonical-compose" * 2)
    token = tmp_path / "user-token"
    token.write_text("fixture-token-only" * 3)
    settings = Settings(
        database=tmp_path / "runs.db",
        signing_key_file=key,
        local_token_file=token,
        consumer=False,
    )
    if sandbox is not None:
        registry.registration = replace(
            registry.registration, runtime=Runtime(RuntimeKind.JOB_WORKER, image=image)
        )

    class ScopedExperience:
        def for_bot(self, bot):
            assert bot == BOT
            return Experience()

    verifier = LocalVerificationService(
        profiles={"default@1": VerificationProfile()},
        cases_for=cases_for,
        execute=execute,
        grade=grade,
    )
    return await compose(
        settings,
        Platform(
            access=None,
            genome=genome,
            registry=registry,
            governance=governance,
            kills=kills,
            strategies=strategies,
            experience=ScopedExperience(),
            sandbox=sandbox,
            verification=verifier,
        ),
    )


@pytest.mark.asyncio
async def test_scheduled_run_reaches_gate_without_moving_baseline(tmp_path) -> None:
    genome = FakeGenome()
    genome.snapshot_files = lambda: None

    async def snapshot(revision, *, bot=None):
        return {"persona/SOUL.md": b"## Truths\n- be useful\n"}

    genome.snapshot_files = snapshot

    # 候选修订版的 id 由提交产生
    async def commit_patch(bot, patch, run_id, strategy_ref, allowed_genes, created_by):
        genome.committed.append((run_id, strategy_ref))
        return "sha256:new"

    genome.commit_patch = commit_patch

    runtime = await _compose(
        tmp_path,
        genome=genome,
        registry=FakeRegistry(registration()),
        governance=FakeGovernance(),
        kills=FakeKills(),
        strategies={"acme/identity-hardening@1.0.0": Strategy},
    )

    try:
        binding = Binding(
            id="bind_01",
            strategy="acme/identity-hardening@1.0.0",
            trigger=EventTrigger(EventName.FAILURE_RATE_ALERT),
            parent="active",
            allowed_genes=["persona"],
            verification_profile="default@1",
            budget=Budget(max_usd=5.0, max_wall_clock_s=3600),
            params={},
        )
        await runtime.api.bindings.put_bindings(
            BOT, [binding], expected_etag=None, actor="u"
        )

        fired = await runtime.runner.scheduler.fire_event(BOT, "bind_01", "evt_1")
        assert fired.status == "fired", fired.reason
        assert await runtime.runner.host.run_once("worker-1") is True

        run = await runtime.api.runs.get(BOT, fired.run_id)
        assert run.status is RunStatus.COMPLETED, runtime.runner.host.last_error
        candidates = await runtime.api.runs.candidates(BOT, fired.run_id)
        assert len(candidates) == 1
        candidate_id = candidates[0].candidate_id

        verdict = await runtime.verifier.settle(
            candidate_id, "default@1", run_id=fired.run_id
        )
        assert verdict.status.value == "accept"

        decision = runtime.api.promotion.store.get(fired.run_id, candidate_id)
        assert decision.outcome is GateOutcome.NEEDS_REVIEW  # persona T2 > frozen T1
        assert (await runtime.api.candidate_report(BOT, candidate_id))[
            "verdict"
        ] == "accept"
        assert runtime.store.candidates(fired.run_id)[0].verdict == "accept"
        assert await runtime.ledger.list(bot=BOT)
        result = await runtime.api.approve_candidate(
            BOT, candidate_id, reason="reviewed", actor=BOT.owner_id
        )
        assert result["review"]["status"] == "approved"
        assert result["promotion"] is None
        with pytest.raises(PromotionNotReady):
            await runtime.api.promotion.promote(
                BOT, candidate_id, run_id=fired.run_id, profile="default@1"
            )
        assert genome.moved == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_container_path_runs_a_worker_in_the_sandbox(tmp_path) -> None:
    from avernet_evolution.core.evolution_run.sandbox import ENV_JOB_TOKEN
    from avernet_evolution.core.evolution_run.worker import Worker

    from .acceptance_fixtures import IMAGE, FakeSandbox, WorkspaceOnlyStrategy

    genome = FakeGenome()

    async def snapshot(revision, *, bot=None):
        return {"persona/SOUL.md": b"## Truths\n- be useful\n"}

    genome.snapshot_files = snapshot
    sandbox = FakeSandbox()
    strategy = WorkspaceOnlyStrategy()
    runtime = await _compose(
        tmp_path,
        genome=genome,
        registry=FakeRegistry(registration()),
        governance=FakeGovernance(),
        kills=FakeKills(),
        strategies={"acme/identity-hardening@1.0.0": strategy},
        sandbox=sandbox,
        image=IMAGE,
    )

    try:
        host = runtime.runner.host.hosts[0]
        assert isinstance(host, ContainerHost)

        binding = Binding(
            id="bind_01",
            strategy="acme/identity-hardening@1.0.0",
            trigger=EventTrigger(EventName.FAILURE_RATE_ALERT),
            parent="active",
            allowed_genes=["persona"],
            verification_profile="default@1",
            budget=Budget(max_usd=5.0, max_wall_clock_s=600),
            params={},
        )
        await runtime.api.bindings.put_bindings(
            BOT, [binding], expected_etag=None, actor="u"
        )

        async def worker() -> None:
            spec = sandbox.created[-1]
            w = Worker(
                transport=runtime.channel,
                strategies={"acme/identity-hardening@1.0.0": strategy},
                worker_id=spec.worker_id,
                heartbeat_s=3600,
            )
            await w.run_once(spec.envs()[ENV_JOB_TOKEN])

        sandbox.on_exec = worker
        host.poll_interval_s = 0.01
        host.settle_s = 2.0
        fired = await runtime.runner.scheduler.fire_event(BOT, "bind_01", "evt_c")
        assert await runtime.runner.host.run_once("host") is True

        run = await runtime.api.runs.get(BOT, fired.run_id)
        assert run.status is RunStatus.COMPLETED
        candidates = await runtime.api.runs.candidates(BOT, fired.run_id)
        assert len(candidates) == 1
        assert sandbox.destroyed
        assert genome.moved == []
    finally:
        await runtime.close()
