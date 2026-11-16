"""运行生命周期、幂等、租约重派发与取消（06 §5–§7）。"""

from __future__ import annotations

import pytest

from agentclaw.community.core.evolution_run import (
    Binding,
    BindingBusy,
    Budget,
    EndReason,
    IdempotencyKeyReused,
    ManualTrigger,
    RunStatus,
    TriggerKind,
    TriggerRecord,
)

from .conftest import BOT

MANUAL = TriggerRecord(kind=TriggerKind.MANUAL, requested_by="user_5")


def binding(**overrides) -> Binding:
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


async def _submit(services, key="k1", **kwargs):
    await services.bindings.put_bindings(
        BOT, [binding()], expected_etag=None, actor="u"
    )
    return await services.runs.submit(
        BOT, "bind_01", idempotency_key=key, trigger=MANUAL, **kwargs
    )


@pytest.mark.asyncio
async def test_submit_is_idempotent(services) -> None:
    run_id = await _submit(services, key="nightly")
    again = await services.runs.submit(
        BOT, "bind_01", idempotency_key="nightly", trigger=MANUAL
    )
    assert again == run_id
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.QUEUED
    assert run.parent_revision.startswith("sha256:")
    assert "candidates@1" in run.granted and "models@1" in run.granted


@pytest.mark.asyncio
async def test_same_key_different_body_is_rejected(services) -> None:
    await _submit(services, key="dup")
    with pytest.raises(IdempotencyKeyReused):
        await services.runs.submit(
            BOT,
            "bind_01",
            idempotency_key="dup",
            trigger=MANUAL,
            params={"window_days": 30},
        )


@pytest.mark.asyncio
async def test_second_run_while_active_is_busy(services) -> None:
    await _submit(services, key="one")
    with pytest.raises(BindingBusy):
        await services.runs.submit(
            BOT, "bind_01", idempotency_key="two", trigger=MANUAL
        )


@pytest.mark.asyncio
async def test_claim_then_complete(services) -> None:
    run_id = await _submit(services)
    claimed = await services.jobs.claim("worker-1", ["acme/identity-hardening@1.0.0"])
    assert claimed is not None
    assert claimed.run.status is RunStatus.RUNNING
    assert claimed.job.fencing_token
    state = await services.jobs.heartbeat(claimed.job.id, claimed.job.fencing_token)
    assert state.cancelled is False
    await services.jobs.complete(
        claimed.job.id,
        claimed.job.fencing_token,
        {"summary": "done", "metrics": {"rounds": 1.0}},
    )
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.summary["summary"] == "done"


@pytest.mark.asyncio
async def test_stale_fencing_token_is_rejected(services) -> None:
    await _submit(services)
    claimed = await services.jobs.claim("worker-1", ["acme/identity-hardening@1.0.0"])
    from agentclaw.community.core.evolution_run import StaleFencingToken

    with pytest.raises(StaleFencingToken):
        await services.jobs.heartbeat(claimed.job.id, "ft_other")


@pytest.mark.asyncio
async def test_lease_expiry_redispatches_with_next_attempt(services) -> None:
    run_id = await _submit(services)
    first = await services.jobs.claim("worker-1", ["acme/identity-hardening@1.0.0"])
    # 让租约过期
    services.store.conn.execute(
        "UPDATE evolution_job SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
        (first.job.id,),
    )
    services.store.conn.commit()
    affected = await services.jobs.expire_leases()
    assert affected == [run_id]
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.QUEUED and run.attempt == 2
    second = await services.jobs.claim("worker-2", ["acme/identity-hardening@1.0.0"])
    assert second is not None and second.job.attempt == 2
    assert second.job.fencing_token != first.job.fencing_token


@pytest.mark.asyncio
async def test_max_attempts_fails_the_run(services) -> None:
    run_id = await _submit(services)
    for attempt in range(3):
        claimed = await services.jobs.claim(
            f"w{attempt}", ["acme/identity-hardening@1.0.0"]
        )
        services.store.conn.execute(
            "UPDATE evolution_job SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
            (claimed.job.id,),
        )
        services.store.conn.commit()
        await services.jobs.expire_leases()
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.FAILED
    assert run.end_reason is EndReason.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_retryable_failure_requeues(services) -> None:
    run_id = await _submit(services)
    claimed = await services.jobs.claim("w1", ["acme/identity-hardening@1.0.0"])
    await services.jobs.fail(
        claimed.job.id, claimed.job.fencing_token, reason="transient", retryable=True
    )
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.QUEUED and run.attempt == 2
    # §7.2：重新排队必须真的能被再次认领（否则运行会永久卡在 queued）
    again = await services.jobs.claim("w2", ["acme/identity-hardening@1.0.0"])
    assert again is not None and again.job.attempt == 2


@pytest.mark.asyncio
async def test_retryable_failure_on_the_last_attempt_fails_the_run(services) -> None:
    run_id = await _submit(services)
    for attempt in range(3):
        claimed = await services.jobs.claim(
            f"w{attempt}", ["acme/identity-hardening@1.0.0"]
        )
        await services.jobs.fail(
            claimed.job.id,
            claimed.job.fencing_token,
            reason="transient",
            retryable=True,
        )
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.FAILED
    assert run.end_reason is EndReason.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_newly_requeued_job_is_not_immediately_expired(services) -> None:
    """新排队的作业没有租约；否则会被下一轮清扫当作过期再烧掉一次尝试。"""

    run_id = await _submit(services)
    claimed = await services.jobs.claim("w1", ["acme/identity-hardening@1.0.0"])
    await services.jobs.fail(
        claimed.job.id, claimed.job.fencing_token, reason="transient", retryable=True
    )
    affected = await services.jobs.expire_leases()
    assert affected == []
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.QUEUED and run.attempt == 2


@pytest.mark.asyncio
async def test_cancel_ends_run_and_finishes_operations(services) -> None:
    run_id = await _submit(services)
    from agentclaw.community.core.evolution_run import OperationKind

    op_id = await services.operations.start(
        run_id, OperationKind.AGENT_SESSION, idempotency_key=f"{run_id}/fix", request={}
    )
    cancelled = await services.runs.cancel(BOT, run_id, reason="owner", actor="user_5")
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.end_reason is EndReason.CANCELLED_BY_OWNER
    op = await services.operations.get(run_id, op_id)
    from agentclaw.community.core.evolution_run import OperationStatus

    assert op.status is OperationStatus.CANCELLED
    again = await services.runs.cancel(BOT, run_id, reason="again", actor="user_5")
    assert again.status is RunStatus.CANCELLED  # 幂等


@pytest.mark.asyncio
async def test_drift_records_a_cancelled_run(services) -> None:
    services.genome.drifted = True
    run_id = await _submit(services)
    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.CANCELLED
    assert run.end_reason is EndReason.OUT_OF_BAND_CHANGE
    assert run.parent_revision == ""
