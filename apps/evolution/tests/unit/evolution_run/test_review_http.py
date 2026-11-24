"""09 §10.1 的 HTTP 面：路径、信封与错误码。"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from avernet_evolution.adapters.control import BASE, build_control_router, install_errors

from .conftest import BOT
from .test_review_api import CANDIDATE, _stack


class _Access:
    def __init__(self, bot, actor: str = "user_17") -> None:
        self.bot = bot
        self.actor = actor


class _AccessPort:
    async def authorize(self, *, bot_id, owner_hint, credentials, write):
        del bot_id, owner_hint, credentials, write
        return _Access(BOT)


def _client(api) -> httpx.AsyncClient:
    runtime = SimpleNamespace(api=api, platform=SimpleNamespace(access=_AccessPort()))
    app = FastAPI()
    install_errors(app)
    app.include_router(build_control_router(runtime))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://platform.test")


@pytest.mark.asyncio
async def test_report_and_queue_over_http(services) -> None:
    api, promotion, _run_id = await _stack(services)
    await promotion.decide(BOT, CANDIDATE)

    async with _client(api) as client:
        report = await client.get(f"{BASE.format(bot_id=BOT.bot_id)}/candidates/{CANDIDATE}")
        queue = await client.get(f"{BASE.format(bot_id=BOT.bot_id)}/review-queue")

    assert report.status_code == 200
    assert report.json()["data"]["verdict"] == "accept"
    assert report.json()["data"]["gate"]["outcome"] == "needs_review"
    assert queue.status_code == 200
    assert queue.json()["data"]["total"] == 1


@pytest.mark.asyncio
async def test_unknown_candidate_is_404_not_found(services) -> None:
    api, _promotion, _run_id = await _stack(services)

    async with _client(api) as client:
        response = await client.get(f"{BASE.format(bot_id=BOT.bot_id)}/candidates/sha256:nope")

    assert response.status_code == 404
    assert response.json()["code"] == 404000


@pytest.mark.asyncio
async def test_approve_then_retry_with_same_key(services) -> None:
    api, promotion, _run_id = await _stack(services)
    await promotion.decide(BOT, CANDIDATE)
    url = f"{BASE.format(bot_id=BOT.bot_id)}/candidates/{CANDIDATE}:approve"

    async with _client(api) as client:
        first = await client.post(url, json={"reason": "diff reviewed"}, headers={"Idempotency-Key": "k1"})
        again = await client.post(url, json={"reason": "diff reviewed"}, headers={"Idempotency-Key": "k1"})

    assert first.status_code == 200
    assert first.json()["data"]["review"]["status"] == "approved"
    assert first.json()["data"]["promotion"] is None
    # 每次请求的 request_id 不同；幂等保证的是 data 相同。
    assert again.json()["data"] == first.json()["data"]


@pytest.mark.asyncio
async def test_reject_requires_a_reason(services) -> None:
    api, promotion, _run_id = await _stack(services)
    await promotion.decide(BOT, CANDIDATE)
    url = f"{BASE.format(bot_id=BOT.bot_id)}/candidates/{CANDIDATE}:reject"

    async with _client(api) as client:
        empty = await client.post(url, json={"reason": ""})
        ok = await client.post(url, json={"reason": "drops the legal-hold exception"})

    assert empty.status_code == 422
    assert ok.status_code == 200
    assert ok.json()["data"]["review"]["status"] == "rejected"
