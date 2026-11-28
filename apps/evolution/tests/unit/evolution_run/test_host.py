"""进程内宿主：真实上下文 + 策略端到端（06 §9）。"""

from __future__ import annotations

import pytest

from avernet_evolution.core.evolution_run import (
    Binding,
    Budget,
    InProcessHost,
    ManualTrigger,
    RunStatus,
    TriggerKind,
    TriggerRecord,
    build_run_context,
)
from avernet_evolution.core.evolution_strategy import Candidate, RunSummary

from .conftest import BOT

MANUAL = TriggerRecord(kind=TriggerKind.MANUAL, requested_by="user_5")


class HardenStrategy:
    """最小策略：在 persona 里追加一行并提交候选。"""

    async def run(self, ctx):
        ctx.requires("experience.sessions@1")
        ctx.requires("candidates@1")
        episodes = await ctx.experience.sessions(days=7, limit=10)
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/r1")
        current = (await ws.read("persona/SOUL.md")).decode("utf-8")
        await ws.write("persona/SOUL.md", current + "- prefer plain language\n")
        patch = await ws.to_patch(rationale="harden", evidence=("episode:ep_1",))
        await ctx.candidates.submit(
            Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
        )
        return RunSummary(
            summary="submitted",
            metrics={"episodes": float(len(episodes)), "candidate": 1.0},
        )


class FakeExperience:
    async def sessions(self, *, days, limit=500, revision=None):
        return [object()]

    async def feedback(self, *, days, kinds=None, limit=500):
        return []


async def _snapshot(revision, *, bot=None):
    return {"persona/SOUL.md": b"## Core Truths\n- be useful\n"}


def _binding(**overrides) -> Binding:
    values = {
        "id": "bind_01",
        "strategy": "acme/identity-hardening@1.0.0",
        "trigger": ManualTrigger(),
        "parent": "active",
        "allowed_genes": ["persona"],
        "verification_profile": "default@1",
        "budget": Budget(max_usd=5.0, max_wall_clock_s=3600),
        "params": {},
    }
    values.update(overrides)
    return Binding(**values)


@pytest.mark.asyncio
async def test_host_runs_a_strategy_to_completion(services) -> None:
    services.genome.snapshot_files = _snapshot

    await services.bindings.put_bindings(BOT, [_binding()], expected_etag=None, actor="u")
    run_id = await services.runs.submit(BOT, "bind_01", idempotency_key="k", trigger=MANUAL)

    def factory(run, **kwargs):
        return build_run_context(
            run,
            store=services.store,
            genome=services.genome,
            budget=services.budget,
            operations=services.operations,
            **kwargs,
        )

    host = InProcessHost(
        strategies={"acme/identity-hardening@1.0.0": HardenStrategy},
        store=services.store,
        jobs=services.jobs,
        operations=services.operations,
        budget=services.budget,
        genome=services.genome,
        ctx_factory=factory,
        experience_provider=FakeExperience(),
        heartbeat_s=3600,
    )
    assert await host.run_once("worker-1") is True
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.candidate_count == 1
    candidates = await services.runs.candidates(BOT, run_id)
    assert len(candidates) == 1 and candidates[0].verdict == "pending"
    assert len(services.genome.committed) == 1
