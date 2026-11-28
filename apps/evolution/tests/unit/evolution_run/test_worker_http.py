"""作业协议的 HTTP 线（06 §7）：真实 ASGI 往返的 worker 端到端。"""

from __future__ import annotations

import httpx
import pytest

from avernet_evolution.adapters.jobs import build_job_app
from avernet_evolution.core.evolution_run import (
    Binding,
    Budget,
    JobChannel,
    ManualTrigger,
    RunStatus,
    TriggerRecord,
    Worker,
)
from avernet_evolution.core.evolution_run.errors import CandidateRejected, CandidateRejectionCode
from avernet_evolution.plugins.job_http import HttpWorkerTransport

from .conftest import BOT
from .test_worker import REF, TOKEN_ISSUER, WorkspaceOnlyStrategy

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


async def _wired(services) -> tuple[Worker, httpx.AsyncClient]:
    channel = JobChannel(
        store=services.store,
        jobs=services.jobs,
        genome=services.genome,
        tokens=TOKEN_ISSUER,
    )
    app = build_job_app(channel)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test")
    transport = HttpWorkerTransport("http://platform.test", client=client)
    worker = Worker(
        transport=transport,
        strategies={REF: WorkspaceOnlyStrategy},
        worker_id="w1",
        heartbeat_s=3600,
    )
    return worker, client


@pytest.mark.asyncio
async def test_worker_runs_a_job_over_http(services) -> None:
    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor="u")
    run_id = await services.runs.submit(
        BOT,
        "bind_01",
        idempotency_key="k1",
        trigger=TriggerRecord(kind="manual", fire_time=None),
    )
    worker, client = await _wired(services)
    async with client:
        assert await worker.run_once(TOKEN_ISSUER.issue("w1")) is True
        assert await worker.run_once(TOKEN_ISSUER.issue("w1")) is False

    run = await services.runs.get(BOT, run_id)
    assert run.status is RunStatus.COMPLETED
    assert len(await services.runs.candidates(BOT, run_id)) == 1


@pytest.mark.asyncio
async def test_genome_rejection_is_a_typed_error_not_a_blob() -> None:
    """422 `candidate_rejected` 必须还原成类型化异常，策略与 worker 才能区分。"""

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.post("/jobs/{job_id}/candidates")
    async def reject(job_id: str) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "candidate_rejected", "reason": "already_recorded"}}, status_code=422
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://platform.test"
    ) as client:
        transport = HttpWorkerTransport("http://platform.test", client=client)
        with pytest.raises(CandidateRejected) as exc:
            await transport.submit("job_1", "ft_1", {})

    assert exc.value.code is CandidateRejectionCode.ALREADY_RECORDED


@pytest.mark.asyncio
async def test_http_claim_rejects_a_forged_token(services) -> None:
    worker, client = await _wired(services)
    async with client:
        with pytest.raises(PermissionError):
            await worker.run_once("forged")


@pytest.mark.asyncio
async def test_http_files_and_verdict_round_trip(services) -> None:
    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor="u")
    await services.runs.submit(
        BOT,
        "bind_01",
        idempotency_key="k1",
        trigger=TriggerRecord(kind="manual", fire_time=None),
    )
    channel = JobChannel(
        store=services.store,
        jobs=services.jobs,
        genome=services.genome,
        tokens=TOKEN_ISSUER,
    )
    app = build_job_app(channel)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://platform.test"
    ) as client:
        transport = HttpWorkerTransport("http://platform.test", client=client)
        grant = await transport.claim("w1", TOKEN_ISSUER.issue("w1"), [REF])
        assert grant is not None and grant["strategy_ref"] == REF
        files = await transport.files(grant["job_id"], grant["fencing_token"])
        assert files == {}
