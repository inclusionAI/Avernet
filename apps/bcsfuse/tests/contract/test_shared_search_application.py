"""TC v2 is an explicit composition; v1 and Profile identity remain intact."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from search_core.api.errors import SearchOverloaded, SearchUnavailable
from search_core.api.models import SearchCommand, VerifiedIdentity
from search_core.adapters.http import create_search_router
from fastapi import Request
from fastapi.testclient import TestClient
from app.application.search.service import TcSearchApplication
from app.bootstrap.app_factory import create_bcsfuse_app
from app.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)

from tests.unit.bootstrap.test_app_factory import build_context


class Candidates:
    def __init__(self):
        self.calls = []

    def recommend(self, **kwargs):
        self.calls.append(kwargs)
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            mode=kwargs["mode"],
            recommendations=[
                CandidateRecommendation(
                    profile_key="bot:owner:profile",
                    worker_id="bot:owner",
                    score=0.8,
                    short_profile="TC profile",
                )
            ],
            total_candidates=1,
            selected_candidates=1,
        )


async def authenticate(request: Request):
    return VerifiedIdentity("caller", frozenset({"bot:owner:profile"}))


def test_optional_common_routes_preserve_tc_identity_and_v1_surface(monkeypatch):
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    candidates = Candidates()
    with ThreadPoolExecutor(max_workers=2) as executor:
        service = TcSearchApplication(candidates, executor=executor)
        context, _ = build_context()
        router = create_search_router(service, authenticate)
        with TestClient(create_bcsfuse_app(context, search_router=router)) as client:
            result = client.post("/api/v2/search", json={"query": "助手"})
            assert result.status_code == 200
            item = result.json()["items"][0]
            assert item["id"] == "bot:owner:profile"
            assert item["extensions"] == {
                "worker_id": "bot:owner",
                "profile_key": "bot:owner:profile",
            }
            paths = client.get("/openapi.json").json()["paths"]
            assert "/api/v1/recommend" in paths
            assert "/api/v1/groups/{group_id}/fuse" in paths
    assert candidates.calls[0]["filters"]["profile_key"] == ["bot:owner:profile"]


def test_tc_factory_does_not_enable_common_routes_implicitly():
    context, _ = build_context()
    assert (
        TestClient(create_bcsfuse_app(context))
        .post("/api/v2/search", json={"query": "x"})
        .status_code
        == 404
    )


def test_cancelled_request_retains_capacity_until_sync_work_finishes():
    started = threading.Event()
    release = threading.Event()

    class BlockingCandidates(Candidates):
        def recommend(self, **kwargs):
            started.set()
            assert release.wait(5)
            return super().recommend(**kwargs)

    async def exercise(executor):
        candidates = BlockingCandidates()
        service = TcSearchApplication(candidates, executor=executor, max_inflight=1)
        command = SearchCommand("助手", 5, "search")
        identity = VerifiedIdentity("caller", None)
        task = asyncio.create_task(service.execute(command, identity))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(SearchOverloaded):
                await service.execute(command, identity)
        finally:
            release.set()
        # A barrier on this one-thread executor waits for work AND its callbacks.
        await asyncio.wrap_future(executor.submit(lambda: None))
        assert (await service.execute(command, identity)).items[
            0
        ].item_id == "bot:owner:profile"

    with ThreadPoolExecutor(max_workers=1) as executor:
        asyncio.run(exercise(executor))


def test_closed_executor_reports_unavailable():
    executor = ThreadPoolExecutor(max_workers=1)
    executor.shutdown()
    service = TcSearchApplication(Candidates(), executor=executor)
    with pytest.raises(SearchUnavailable):
        asyncio.run(
            service.execute(
                SearchCommand("助手", 5, "search"), VerifiedIdentity("caller", None)
            )
        )


@pytest.mark.parametrize("enabled", [True, False])
def test_rerank_option_disables_or_inherits_host_configuration(enabled):
    candidates = Candidates()
    with ThreadPoolExecutor(max_workers=1) as executor:
        service = TcSearchApplication(candidates, executor=executor)
        asyncio.run(
            service.execute(
                SearchCommand(
                    "助手", 5, "recommend", extensions={"enable_rerank": enabled}
                ),
                VerifiedIdentity("caller", None),
            )
        )
    config = candidates.calls[0]["runtime_config"]
    if enabled:
        assert "reranker_model" not in config
    else:
        assert config["reranker_model"] is None
