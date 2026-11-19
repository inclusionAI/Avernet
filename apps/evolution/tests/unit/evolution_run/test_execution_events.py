"""Execution metadata survives restart, rejects stale workers and fails visibly."""

import json
import sqlite3
from unittest.mock import Mock

import pytest

from avernet_evolution.core.evolution_run import EvolutionStore, JobUnauthorized, Worker
from avernet_evolution.core.evolution_run.events import ExecutionEvents, ExecutionEventWriteError

from .test_sandbox import FakeSandbox, _host, _queued_run
from .test_worker import REF, TOKEN_ISSUER, WorkspaceOnlyStrategy, _channel


def test_journal_restart_scope_and_readonly(tmp_path):
    database = tmp_path / "runs.db"
    store = EvolutionStore(database)
    store.events.append("sandbox.created", worker_id="w1", sandbox_id="s1")
    store.events.append("job.claimed", run_id="r1", job_id="j1", worker_id="w1", attempt=1)
    store.events.append("job.claimed", run_id="r2", job_id="j2", worker_id="w2", attempt=1)
    store.events.append("sandbox.destroyed", worker_id="w1", sandbox_id="s1")
    store.conn.close()
    conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    events = ExecutionEvents(conn, initialize=False)
    rows = events.for_run("r1")
    assert [r["kind"] for r in rows] == ["sandbox.created", "job.claimed", "sandbox.destroyed"]
    assert [r["id"] for r in events.for_run("r1", after=rows[0]["id"])] == [r["id"] for r in rows[1:]]
    assert events.for_run("missing") == []
    with pytest.raises(ExecutionEventWriteError):
        events.append("job.completed", run_id="r1")
    conn.close()


@pytest.mark.asyncio
async def test_real_worker_events_are_correlated_and_metadata_only(services):
    run = await _queued_run(services)
    channel = await _channel(services)
    worker = Worker(channel, {REF: WorkspaceOnlyStrategy}, "worker1")
    assert await worker.run_once(TOKEN_ISSUER.issue("worker1"))
    rows = services.store.events.for_run(run.id)
    kinds = [r["kind"] for r in rows]
    assert kinds == [
        "run.queued",
        "job.claimed",
        "worker.strategy.started",
        "candidate.persisted",
        "worker.strategy.finished",
        "job.completed",
    ]
    claimed = rows[1]
    assert claimed["job_id"] == f"{run.id}_1" and claimed["attempt"] == 1
    assert claimed["worker_id"] == "worker1"
    assert rows[2]["authority"] == "worker-reported"
    serialized = json.dumps(rows)
    assert TOKEN_ISSUER.issue("worker1") not in serialized
    assert "fencing_token" not in serialized and "plain language" not in serialized


@pytest.mark.asyncio
async def test_logs_are_hashed_and_stale_fence_cannot_write(services):
    run = await _queued_run(services)
    channel = await _channel(services)
    grant = await channel.claim("worker1", TOKEN_ISSUER.issue("worker1"), [REF])
    await channel.log(
        grant["job_id"],
        grant["fencing_token"],
        [{"msg": "private transcript", "fields": {"cookie": "never persist"}}],
    )
    rows = services.store.events.for_run(run.id)
    assert rows[-1]["kind"] == "worker.log.received"
    assert "private transcript" not in json.dumps(rows) and "cookie" not in json.dumps(rows)
    with pytest.raises(JobUnauthorized):
        await channel.log(grant["job_id"], "wrong-fence", [{"msg": "strategy.finished"}])
    assert len(services.store.events.for_run(run.id)) == len(rows)


@pytest.mark.asyncio
async def test_event_failure_still_destroys_the_sandbox(services):
    await _queued_run(services)
    sandbox = FakeSandbox()
    services.store.events.append = Mock(side_effect=ExecutionEventWriteError("disk unavailable"))
    with pytest.raises(ExecutionEventWriteError):
        await _host(services, sandbox).run_once("host")
    assert sandbox.destroyed == ["sbx_1"]
    assert not sandbox.executed  # no work after failed create evidence


@pytest.mark.asyncio
async def test_destroy_failure_is_not_reported_as_success(services):
    run = await _queued_run(services)
    sandbox = FakeSandbox()

    async def execute():
        claimed = await services.jobs.claim(sandbox.created[0].worker_id, [REF])
        await services.jobs.complete(claimed.job.id, claimed.job.fencing_token, {})

    sandbox.on_exec = execute

    async def destroy(_):
        raise RuntimeError("delete refused")

    sandbox.destroy = destroy
    with pytest.raises(RuntimeError, match="delete refused"):
        await _host(services, sandbox).run_once("host")
    kinds = [e["kind"] for e in services.store.events.for_run(run.id)]
    assert "sandbox.destroy_failed" in kinds and "sandbox.destroyed" not in kinds


def test_raw_event_fields_are_rejected():
    store = EvolutionStore()
    with pytest.raises(ValueError, match="unsafe"):
        store.events.append("job.claimed", details={"token": "do-not-store"})
    store.conn.close()


@pytest.mark.asyncio
async def test_exec_timeout_destroys_before_requeue_and_records_real_next_job(services):
    run = await _queued_run(services)
    sandbox = FakeSandbox()

    async def execute():
        await services.jobs.claim(sandbox.created[0].worker_id, [REF])
        raise TimeoutError("worker did not exit")

    sandbox.on_exec = execute
    host = _host(services, sandbox, settle_s=0)
    await host.run_once("host")
    assert sandbox.destroyed == ["sbx_1"]
    rows = services.store.events.for_run(run.id)
    kinds = [row["kind"] for row in rows]
    assert kinds.index("sandbox.destroyed") < kinds.index("job.requeued")
    assert rows[-1]["details"]["next_job_id"] == f"{run.id}_2"
    claim = await services.jobs.claim("next-worker", [REF])
    assert claim.job.id == rows[-1]["details"]["next_job_id"]
