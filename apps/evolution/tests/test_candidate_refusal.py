"""Genome rejection contract through the HTTP provider and actual Run consumer."""

import json
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest
from avernet_genome.canonical import sha256_digest
from avernet_genome.patch_models import GenomePatch

from avernet_evolution.adapters.jobs import build_job_app
from avernet_evolution.composition import compose
from avernet_evolution.core.evolution_run.errors import CandidateRejected, CandidateRejectionCode
from avernet_evolution.core.evolution_run.events import ExecutionEvents, ExecutionEventWriteError
from avernet_evolution.local import BOT, STRATEGY, build_local_platform
from avernet_evolution.plugins.platform_http import BotHttp, HttpGenome, PlatformCredential, PlatformHttpError

BINDING = {
    "id": "identity",
    "strategy": STRATEGY,
    "trigger": {"manual": True},
    "parent": "active",
    "allowed_genes": ["persona"],
    "verification_profile": "pending@1",
    "budget": {"max_usd": 0.1, "max_wall_clock_s": 60},
    "auto_promote_ceiling": None,
}


async def headers(bot):
    assert bot == BOT
    return PlatformCredential({}, "delegating-member")


def patch(base):
    return GenomePatch.from_json(
        {
            "patch_schema": 1,
            "base": base,
            "ops": [
                {
                    "op": "file.edit",
                    "target": "persona/SOUL.md",
                    "edits": [{"kind": "insert_after", "anchor": "# Bot", "content": "A truth.\n"}],
                }
            ],
            "rationale": "document a truth",
            "evidence": [],
        }
    )


def refusal(reasons=None):
    return {
        "code": 422000,
        "message": "private-provider-message",
        "data": {
            "reasons": reasons
            if reasons is not None
            else [{"code": "already_recorded", "message": "private-message", "location": "private-path"}]
        },
    }


@pytest.mark.asyncio
async def test_genome_candidate_wire_happy_path_and_known_refusal():
    proposal = patch(sha256_digest(b"parent"))
    rid = sha256_digest(b"candidate")
    calls = []

    def handle(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(201, json={"code": 201000, "data": {"id": rid, "status": "candidate"}})
        return httpx.Response(422, json=refusal())

    async with httpx.AsyncClient(base_url="http://platform.test", transport=httpx.MockTransport(handle)) as c:
        genome = HttpGenome(BotHttp(c, headers), AsyncMock())
        args = (BOT, proposal, "run-one", STRATEGY, ("persona",), STRATEGY)
        assert await genome.commit_patch(*args) == rid
        with pytest.raises(CandidateRejected) as exc:
            await genome.commit_patch(*args)
        assert exc.value.code is CandidateRejectionCode.ALREADY_RECORDED
        assert str(exc.value) == "candidate rejected: already_recorded"
    assert len(calls) == 2
    assert calls[0].method == "POST"
    assert calls[0].url.path == f"/openapi/v1/bots/{BOT.bot_id}/genome/revisions"
    assert json.loads(calls[0].content) == {
        "status": "candidate",
        "run": {"run_id": "run-one", "strategy": STRATEGY, "allowed_genes": ["persona"]},
        "patch": proposal.to_json(),
    }


@pytest.mark.parametrize(
    "method,suffix,status,body",
    [
        ("POST", "/genome/revisions", 422, b"private-not-json"),
        ("POST", "/genome/revisions", 422, b"[]"),
        ("POST", "/genome/revisions", 422, b'{"data":null}'),
        ("POST", "/genome/revisions", 422, json.dumps(refusal([])).encode()),
        ("POST", "/genome/revisions", 422, json.dumps(refusal([{"code": "unknown"}])).encode()),
        ("POST", "/genome/revisions", 422, json.dumps(refusal([{"code": "already_recorded"}, {}])).encode()),
        ("POST", "/genome/revisions", 422, json.dumps(refusal(["already_recorded"])).encode()),
        ("POST", "/genome/revisions", 422, json.dumps(refusal() | {"code": 403000}).encode()),
        ("POST", "/genome/revisions", 422, json.dumps(refusal() | {"padding": "x" * 65536}).encode()),
        ("GET", "/genome/revisions", 422, json.dumps(refusal()).encode()),
        ("POST", "/genome/capture", 422, json.dumps(refusal()).encode()),
        ("POST", "/genome/revisions", 403, json.dumps(refusal()).encode()),
        ("POST", "/genome/revisions", 200, json.dumps(refusal()).encode()),
    ],
)
@pytest.mark.asyncio
async def test_unknown_or_out_of_scope_rejections_remain_safe_generic(method, suffix, status, body):
    async with httpx.AsyncClient(
        base_url="http://platform.test",
        transport=httpx.MockTransport(lambda req: httpx.Response(status, content=body)),
    ) as c:
        with pytest.raises(PlatformHttpError) as exc:
            await BotHttp(c, headers).request(BOT, method, suffix)
        assert "private" not in str(exc.value) and "already_recorded" not in str(exc.value)


@pytest.mark.asyncio
async def test_run_refusal_durable_no_fake_candidate_or_ledger(settings):
    platform = await build_local_platform(settings)
    platform.genome.commit_patch = AsyncMock(
        side_effect=CandidateRejected(CandidateRejectionCode.ALREADY_RECORDED)
    )
    runtime = await compose(settings, platform)
    try:
        before = (await platform.genome.resolve_revision(BOT, "active")).revision.id
        await runtime.api.put_bindings(BOT, [BINDING], expected_etag=None, actor=BOT.owner_id)
        run_id = (
            await runtime.api.submit_run(
                BOT, {"binding_id": "identity", "idempotency_key": "collision"}, actor=BOT.owner_id
            )
        )["run"]
        assert await runtime.runner.tick() == 1
        platform.genome.commit_patch.assert_awaited_once()
        run = (await runtime.api.get_run(BOT, run_id))["run"]
        assert run["candidate_count"] == 0
        events = (await runtime.api.run_evidence(BOT, run_id))["events"]
        refused = [e for e in events if e["kind"] == "candidate.rejected"]
        assert len(refused) == 1
        assert refused[0]["details"]["rejection_code"] == "already_recorded"
        assert refused[0]["authority"] == "platform"
        assert not any(e["kind"] in {"candidate.persisted", "ledger.submission.persisted"} for e in events)
        assert runtime.ledger.conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0] == 0
        rows = (await runtime.api.run_candidates(BOT, run_id))["candidates"]
        assert all(not row["revision"] and row["verdict"] == "pending" for row in rows)
        assert (await platform.genome.resolve_revision(BOT, "active")).revision.id == before
    finally:
        await runtime.close()
    runtime = await compose(settings, await build_local_platform(settings))
    try:
        assert any(e["kind"] == "candidate.rejected" for e in runtime.store.events.for_run(run_id))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_job_http_maps_only_allowlisted_domain_refusal():
    channel = AsyncMock()
    channel.submit.side_effect = CandidateRejected(CandidateRejectionCode.ALREADY_RECORDED)
    async with httpx.AsyncClient(
        base_url="http://jobs.test", transport=httpx.ASGITransport(app=build_job_app(channel))
    ) as client:
        response = await client.post("/jobs/j/candidates", json={"fencing_token": "test-fence", "patch": {}})
        assert response.status_code == 422
        assert response.json() == {"error": {"code": "candidate_rejected", "reason": "already_recorded"}}
    channel.submit.assert_awaited_once_with("j", "test-fence", {})


def test_rejection_event_code_validation_and_failed_write():
    conn = sqlite3.connect(":memory:")
    events = ExecutionEvents(conn)
    try:
        with pytest.raises(ValueError):
            events.append("candidate.rejected", details={"rejection_code": "private-error"})
        conn.execute("PRAGMA query_only=ON")
        with pytest.raises(ExecutionEventWriteError):
            events.append("candidate.rejected", details={"rejection_code": "already_recorded"})
    finally:
        conn.close()
