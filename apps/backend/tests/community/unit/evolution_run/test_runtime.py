"""组合根：调度 → 执行 → 候选 → 验证 → 门禁 的端到端装配。"""

from __future__ import annotations


import pytest

from agentclaw.community.core.evolution_run import (
    Binding,
    Budget,
    ContainerHost,
    EventTrigger,
    EventName,
    RunStatus,
    build_evolution_runtime,
)
from agentclaw.community.core.evolution_verify import (
    Case,
    CaseCategory,
    CaseOrigin,
    CaseOriginKind,
    Grade,
    RolloutRecord,
    Split,
    VerificationProfile,
)

from .conftest import (
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
        from agentclaw.community.core.evolution_strategy import Candidate, RunSummary

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


@pytest.mark.asyncio
async def test_scheduled_run_reaches_promotion() -> None:
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

    runtime = build_evolution_runtime(
        genome=genome,
        registry=FakeRegistry(registration()),
        governance=FakeGovernance(),
        kills=FakeKills(),
        strategies={"acme/identity-hardening@1.0.0": Strategy},
        profiles={"default@1": VerificationProfile()},
        cases_for=cases_for,
        execute=execute,
        grade=grade,
        experience=Experience(),
        heartbeat_s=3600,
    )

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
    await runtime.bindings.put_bindings(BOT, [binding], expected_etag=None, actor="u")

    fired = await runtime.scheduler.fire_event(BOT, "bind_01", "evt_1")
    assert fired.status == "fired", fired.reason
    assert await runtime.host.run_once("worker-1") is True

    run = await runtime.runs.get(BOT, fired.run_id)
    assert run.status is RunStatus.COMPLETED, runtime.host.last_error
    candidates = await runtime.runs.candidates(BOT, fired.run_id)
    assert len(candidates) == 1
    candidate_id = candidates[0].candidate_id

    verdict = await runtime.verification.settle(candidate_id, "default@1")
    assert verdict.status.value == "accept"

    decision = await runtime.promotion.decide(BOT, candidate_id)
    assert decision.outcome.value == "auto_promote"
    assert ("active", "sha256:new") in genome.moved


@pytest.mark.asyncio
async def test_runtime_container_path_runs_a_worker_in_the_sandbox() -> None:
    from agentclaw.community.core.evolution_run.sandbox import ENV_JOB_TOKEN
    from agentclaw.community.core.evolution_run.worker import Worker

    from .test_sandbox import IMAGE, FakeSandbox
    from .test_worker import WorkspaceOnlyStrategy

    genome = FakeGenome()

    async def snapshot(revision, *, bot=None):
        return {"persona/SOUL.md": b"## Truths\n- be useful\n"}

    genome.snapshot_files = snapshot
    sandbox = FakeSandbox()
    strategy = WorkspaceOnlyStrategy()
    runtime = build_evolution_runtime(
        genome=genome,
        registry=FakeRegistry(registration()),
        governance=FakeGovernance(),
        kills=FakeKills(),
        strategies={"acme/identity-hardening@1.0.0": strategy},
        profiles={"default@1": VerificationProfile()},
        cases_for=cases_for,
        execute=execute,
        grade=grade,
        experience=Experience(),
        heartbeat_s=3600,
        sandbox_worker=sandbox,
        strategy_images=lambda _run: IMAGE,
    )

    assert isinstance(runtime.host, ContainerHost)

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
    await runtime.bindings.put_bindings(BOT, [binding], expected_etag=None, actor="u")

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
    runtime.host.poll_interval_s = 0.01
    runtime.host.settle_s = 2.0
    fired = await runtime.scheduler.fire_event(BOT, "bind_01", "evt_c")
    assert await runtime.host.run_once("host") is True

    run = await runtime.runs.get(BOT, fired.run_id)
    assert run.status is RunStatus.COMPLETED
    candidates = await runtime.runs.candidates(BOT, fired.run_id)
    assert len(candidates) == 1
    assert sandbox.destroyed
