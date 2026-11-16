"""容器内 worker（06 §7）：经作业协议认领、执行、提交与回写。"""

from __future__ import annotations

import pytest

from agentclaw.community.core.evolution_run import (
    Binding,
    Budget,
    JobChannel,
    JobTokenIssuer,
    JobUnauthorized,
    ManualTrigger,
    RunStatus,
    TriggerRecord,
    Worker,
)
from agentclaw.community.core.evolution_strategy import Candidate, RunSummary

from .conftest import BOT

REF = "acme/identity-hardening@1.0.0"
TOKEN_ISSUER = JobTokenIssuer(secret=b"unit-secret")


class WorkspaceOnlyStrategy:
    async def run(self, ctx):
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/r1")
        await ws.write("persona/SOUL.md", b"## Truths\n- plain language\n")
        patch = await ws.to_patch(rationale="harden")
        await ctx.candidates.submit(
            Candidate(
                patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence)
            )
        )
        return RunSummary(summary="done", metrics={"candidates": 1.0})


BINDING = Binding(
    id="bind_01",
    strategy=REF,
    trigger=ManualTrigger(),
    parent="active",
    allowed_genes=["persona"],
    verification_profile="default@1",
    budget=Budget(max_usd=5.0, max_wall_clock_s=600),
    params={},
)


async def _channel(services) -> JobChannel:
    return JobChannel(
        store=services.store,
        jobs=services.jobs,
        genome=services.genome,
        tokens=TOKEN_ISSUER,
    )


@pytest.mark.asyncio
async def test_worker_claims_runs_and_submits_a_candidate(services) -> None:
    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor="u")
    run_id = await services.runs.submit(
        BOT,
        "bind_01",
        idempotency_key="k1",
        trigger=TriggerRecord(kind="manual", fire_time=None),
    )
    channel = await _channel(services)
    worker = Worker(
        transport=channel,
        strategies={REF: WorkspaceOnlyStrategy},
        worker_id="w1",
        heartbeat_s=3600,
    )

    assert await worker.run_once(TOKEN_ISSUER.issue("w1")) is True

    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.COMPLETED
    candidates = await services.runs.candidates(BOT, run_id)
    assert len(candidates) == 1
    patch = services.genome.committed
    assert patch and patch[0][0] == run_id


@pytest.mark.asyncio
async def test_worker_with_nothing_queued_returns_false(services) -> None:
    channel = await _channel(services)
    worker = Worker(
        transport=channel, strategies={REF: WorkspaceOnlyStrategy}, worker_id="w1"
    )
    assert await worker.run_once(TOKEN_ISSUER.issue("w1")) is False


@pytest.mark.asyncio
async def test_channel_rejects_a_forged_token(services) -> None:
    channel = await _channel(services)
    with pytest.raises(JobUnauthorized):
        await channel.claim("w1", "forged", [REF])


@pytest.mark.asyncio
async def test_channel_files_come_from_the_parent_revision(services) -> None:
    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor="u")
    await services.runs.submit(
        BOT,
        "bind_01",
        idempotency_key="k1",
        trigger=TriggerRecord(kind="manual", fire_time=None),
    )
    channel = await _channel(services)
    grant = await channel.claim("w1", TOKEN_ISSUER.issue("w1"), [REF])
    files = await channel.files(grant["job_id"], grant["fencing_token"])
    assert files == {}


@pytest.mark.asyncio
async def test_channel_submit_rejects_a_patch_for_another_base(services) -> None:
    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor="u")
    await services.runs.submit(
        BOT,
        "bind_01",
        idempotency_key="k1",
        trigger=TriggerRecord(kind="manual", fire_time=None),
    )
    channel = await _channel(services)
    grant = await channel.claim("w1", TOKEN_ISSUER.issue("w1"), [REF])
    patch = {
        "patchSchema": 1,
        "base": "sha256:not-the-parent",
        "ops": [],
        "rationale": "x",
        "evidence": [],
    }
    with pytest.raises(Exception):
        await channel.submit(grant["job_id"], grant["fencing_token"], patch)
