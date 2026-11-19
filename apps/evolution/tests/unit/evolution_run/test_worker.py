"""容器内 worker（06 §7）：经作业协议认领、执行、提交与回写。"""

from __future__ import annotations

import pytest

from avernet_evolution.core.evolution_run import (
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
from avernet_evolution.core.evolution_run.errors import CandidateRejected, CandidateRejectionCode
from avernet_evolution.core.evolution_strategy import Candidate, RunSummary

from .conftest import BOT

REF = "acme/identity-hardening@1.0.0"
TOKEN_ISSUER = JobTokenIssuer(secret=b"unit-secret")


class WorkspaceOnlyStrategy:
    async def run(self, ctx):
        ws = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/r1")
        await ws.write("persona/SOUL.md", b"## Truths\n- plain language\n")
        patch = await ws.to_patch(rationale="harden")
        await ctx.candidates.submit(
            Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
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


class _RejectingChannel:
    """submit 抛 Genome 的 `already_recorded`；其余调用透传给真实通道。"""

    def __init__(self, inner) -> None:
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def submit(self, job_id, fencing_token, patch):
        raise CandidateRejected(CandidateRejectionCode.ALREADY_RECORDED)


@pytest.mark.asyncio
async def test_already_recorded_candidate_completes_the_run(services) -> None:
    """同内容候选已被记录：运行应 completed 收敛，而不是重试到 max_attempts。"""

    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor="u")
    run_id = await services.runs.submit(
        BOT,
        "bind_01",
        idempotency_key="k1",
        trigger=TriggerRecord(kind="manual", fire_time=None),
    )
    worker = Worker(
        transport=_RejectingChannel(await _channel(services)),
        strategies={REF: WorkspaceOnlyStrategy},
        worker_id="w1",
        heartbeat_s=3600,
    )

    assert await worker.run_once(TOKEN_ISSUER.issue("w1")) is True

    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.summary == {
        "summary": "no new candidate: already_recorded",
        "metrics": {"already_recorded": 1.0},
    }
    assert await services.runs.candidates(BOT, run_id) == []
    assert worker.last_error is None


@pytest.mark.asyncio
async def test_worker_with_nothing_queued_returns_false(services) -> None:
    channel = await _channel(services)
    worker = Worker(transport=channel, strategies={REF: WorkspaceOnlyStrategy}, worker_id="w1")
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
        "patch_schema": 1,
        "base": "sha256:" + "f" * 64,
        "ops": [{"op": "persona.add", "type": "SOUL.md", "content": "Be precise.\n"}],
        "rationale": "x",
        "evidence": [],
    }
    with pytest.raises(ValueError, match="not the run's parent revision"):
        await channel.submit(grant["job_id"], grant["fencing_token"], patch)
