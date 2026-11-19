"""作业协议路由（06 §7）：与主应用同一条构造路径（DI 解析器 + 前缀）。"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from avernet_evolution.adapters.jobs import build_job_router
from avernet_evolution.core.evolution_run.job_protocol import JobChannel

from .conftest import BOT
from .test_worker import BINDING, REF, TOKEN_ISSUER

PREFIX = "/openapi/v1/evolution/jobs"


def _app(services) -> FastAPI:
    channel = JobChannel(
        store=services.store, jobs=services.jobs, genome=services.genome, tokens=TOKEN_ISSUER
    )
    app = FastAPI()
    app.include_router(build_job_router(lambda: channel, prefix=PREFIX))
    return app


def _client(services) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(services)), base_url="http://platform.test"
    )


@pytest.mark.asyncio
async def test_claim_returns_204_when_nothing_is_queued(services) -> None:
    async with _client(services) as client:
        response = await client.post(
            f"{PREFIX}/jobs:claim",
            json={"worker_id": "w1", "token": TOKEN_ISSUER.issue("w1"), "strategy_refs": [REF]},
        )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_claim_rejects_a_forged_token(services) -> None:
    async with _client(services) as client:
        response = await client.post(
            f"{PREFIX}/jobs:claim", json={"worker_id": "w1", "token": "forged", "strategy_refs": [REF]}
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "job_unauthorized"


@pytest.mark.asyncio
async def test_claim_returns_the_grant_over_http(services) -> None:
    from avernet_evolution.core.evolution_run import TriggerRecord

    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor="u")
    run_id = await services.runs.submit(
        BOT, "bind_01", idempotency_key="k1", trigger=TriggerRecord(kind="manual", fire_time=None)
    )

    async with _client(services) as client:
        response = await client.post(
            f"{PREFIX}/jobs:claim",
            json={"worker_id": "w1", "token": TOKEN_ISSUER.issue("w1"), "strategy_refs": [REF]},
        )
    assert response.status_code == 200
    grant = response.json()
    assert grant["run_id"] == run_id and grant["strategy_ref"] == REF
    assert grant["granted"]
