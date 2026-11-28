"""Consumer ↔ platform contracts, without Backend on sys.path."""

import json
import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from avernet_evolution.bootstrap import create_app
from avernet_evolution.composition import compose
from avernet_evolution.core.evolution_run.errors import JobNotFound, StaleFencingToken
from avernet_evolution.core.evolution_run.store import utcnow
from avernet_evolution.local import BOT, STRATEGY, build_local_platform

BASE = f"/openapi/v1/bots/{BOT.bot_id}/evolution"
BINDING = {
    "id": "identity",
    "strategy": STRATEGY,
    "trigger": {"manual": True},
    "parent": "active",
    "allowed_genes": ["persona"],
    "verification_profile": "rules-file@1",
    "budget": {"max_usd": 0.1, "max_wall_clock_s": 60},
    "auto_promote_ceiling": None,
}


def client_headers(settings):
    return {"Authorization": f"Bearer {settings.local_token_file.read_text().strip()}"}


def configure(client, settings):
    client.headers.update(client_headers(settings))
    client.params = {"entity_id": BOT.owner_id}
    tag = client.get(BASE + "/bindings").headers["etag"]
    result = client.put(BASE + "/bindings", json={"bindings": [BINDING]}, headers={"If-Match": tag})
    assert result.status_code == 200, result.text
    return result


def test_http_authorization_cas_idempotency_and_restart(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/bindings").status_code == 403
        configured = configure(client, settings)
        assert configured.json()["code"] == 200000
        assert client.put(BASE + "/bindings", json={"bindings": []}).status_code == 428
        assert (
            client.put(BASE + "/bindings", json={"bindings": []}, headers={"If-Match": '"stale"'}).status_code
            == 412
        )
        assert client.get(BASE.replace(BOT.bot_id, "other") + "/runs").status_code == 403
        response = client.post(
            BASE + "/runs", json={"binding_id": "identity"}, headers={"Idempotency-Key": "request1"}
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["data"]["run"]
        assert (
            client.post(
                BASE + "/runs", json={"binding_id": "identity"}, headers={"Idempotency-Key": "request1"}
            ).json()["data"]["run"]
            == run_id
        )
        assert (
            client.post(
                BASE + "/runs",
                json={"binding_id": "identity", "params": {"x": 1}},
                headers={"Idempotency-Key": "request1"},
            ).status_code
            == 409
        )
        evidence = client.get(BASE + f"/runs/{run_id}/evidence").json()["data"]
        assert len(evidence["jobs"]) == 1
        assert evidence["events"][0]["kind"] == "run.queued"
        assert "fencing_token" not in json.dumps(evidence)
        assert response.headers["X-Trace-Id"] == response.json()["request_id"]
    with TestClient(create_app(settings)) as client:
        client.headers.update(client_headers(settings))
        assert (
            client.get(BASE + f"/runs/{run_id}", params={"entity_id": BOT.owner_id}).json()["data"]["run"][
                "status"
            ]
            == "queued"
        )


@pytest.mark.asyncio
async def test_fixture_strategy_really_edits_candidate_and_keeps_active(settings):
    platform = await build_local_platform(settings)
    runtime = await compose(settings, platform)
    try:
        before = await platform.genome.resolve_revision(BOT, "active")
        await runtime.api.put_bindings(BOT, [BINDING], expected_etag=None, actor=BOT.owner_id)
        result = await runtime.api.submit_run(
            BOT, {"binding_id": "identity", "idempotency_key": "a"}, actor=BOT.owner_id
        )
        assert await runtime.runner.tick() == 1
        run_id = result["run"]
        run = await runtime.api.get_run(BOT, run_id)
        assert run["run"]["status"] == "completed", run
        candidates = (await runtime.api.run_candidates(BOT, run_id))["candidates"]
        assert len(candidates) == 1 and candidates[0]["verdict"] == "pending"
        revision = await platform.genome.resolve_revision(BOT, candidates[0]["revision"])
        content = await platform.genome.read_content(BOT, revision.spec.persona[0].digest)
        assert b"Core Truths" in content
        assert (await platform.genome.resolve_revision(BOT, "active")).revision.id == before.revision.id
        assert runtime.store.events.for_run(run_id)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_restart_reclaims_expired_job_and_rejects_old_fence(settings):
    runtime = await compose(settings, await build_local_platform(settings))
    await runtime.api.put_bindings(BOT, [BINDING], expected_etag=None, actor=BOT.owner_id)
    result = await runtime.api.submit_run(
        BOT, {"binding_id": "identity", "idempotency_key": "r"}, actor=BOT.owner_id
    )
    claim = await runtime.runner.jobs.claim("old-worker", [STRATEGY])
    await runtime.close()
    runtime = await compose(settings, await build_local_platform(settings))
    try:
        assert await runtime.runner.jobs.expire_leases(utcnow() + timedelta(minutes=2)) == [result["run"]]
        with pytest.raises((StaleFencingToken, JobNotFound)) as caught:
            await runtime.runner.jobs.complete(claim.job.id, claim.job.fencing_token, {})
        assert type(caught.value).__name__ in {"JobNotFound", "StaleFencingToken"}
        assert await runtime.runner.tick() == 1
        assert (await runtime.api.get_run(BOT, result["run"]))["run"]["status"] == "completed"
        jobs = (await runtime.api.run_evidence(BOT, result["run"]))["jobs"]
        assert [j["attempt"] for j in jobs] == [1, 2]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_atomic_enqueue_rolls_back_run_and_key_if_job_insert_fails(settings):
    runtime = await compose(settings, await build_local_platform(settings))
    try:
        await runtime.api.put_bindings(BOT, [BINDING], expected_etag=None, actor=BOT.owner_id)
        runtime.store.conn.executescript(
            "CREATE TRIGGER reject_job BEFORE INSERT ON evolution_job BEGIN SELECT RAISE(ABORT, 'disk failure'); END;"
        )
        with pytest.raises(sqlite3.IntegrityError):
            await runtime.api.submit_run(
                BOT, {"binding_id": "identity", "idempotency_key": "x"}, actor=BOT.owner_id
            )
        for table in ("evolution_run", "evolution_run_key", "evolution_job"):
            assert runtime.store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_signing_key_change_refused_on_restart(settings):
    runtime = await compose(settings, await build_local_platform(settings))
    await runtime.close()
    settings.signing_key_file.write_bytes(b"different-worker-signing-material" * 2)
    platform = await build_local_platform(settings)
    try:
        with pytest.raises(ValueError, match="immutable deployment identity"):
            await compose(settings, platform)
    finally:
        await platform.close()


@pytest.mark.asyncio
async def test_cancelled_job_never_resurrected_after_restart(settings):
    runtime = await compose(settings, await build_local_platform(settings))
    try:
        await runtime.api.put_bindings(BOT, [BINDING], expected_etag=None, actor=BOT.owner_id)
        result = await runtime.api.submit_run(
            BOT, {"binding_id": "identity", "idempotency_key": "cancel"}, actor=BOT.owner_id
        )
        claim = await runtime.runner.jobs.claim("cancel-worker", [STRATEGY])
        await runtime.api.cancel_run(BOT, result["run"], reason="test", actor=BOT.owner_id)
        await runtime.runner.jobs.complete(claim.job.id, claim.job.fencing_token, {})
        assert (await runtime.api.get_run(BOT, result["run"]))["run"]["status"] == "cancelled"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_artifacts_survive_restart_and_are_run_scoped(settings):
    runtime = await compose(settings, await build_local_platform(settings))
    await runtime.api.put_bindings(BOT, [BINDING], expected_etag=None, actor=BOT.owner_id)
    result = await runtime.api.submit_run(
        BOT, {"binding_id": "identity", "idempotency_key": "artifact"}, actor=BOT.owner_id
    )
    claim = await runtime.runner.jobs.claim("artifact-worker", [STRATEGY])
    artifact_id = await runtime.channel.artifacts_put(
        claim.job.id, claim.job.fencing_token, "../opaque-name", b"retained report"
    )
    await runtime.close()
    runtime = await compose(settings, await build_local_platform(settings))
    try:
        assert runtime.store.get_artifact(result["run"], artifact_id) == b"retained report"
        with pytest.raises(KeyError):
            runtime.store.get_artifact("other-run", artifact_id)
        with pytest.raises(ValueError, match="exceeds limits"):
            runtime.store.put_artifact(result["run"], "too-big", b"x" * (5 * 1024 * 1024 + 1))
        runtime.store.conn.executescript(
            "CREATE TRIGGER reject_artifact BEFORE INSERT ON evolution_artifact "
            "BEGIN SELECT RAISE(ABORT, 'disk failure'); END;"
        )
        with pytest.raises(sqlite3.IntegrityError):
            runtime.store.put_artifact(result["run"], "must-fail", b"no silent success")
    finally:
        await runtime.close()
