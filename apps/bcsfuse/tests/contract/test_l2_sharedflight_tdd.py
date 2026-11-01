"""TDD contract for coalescing concurrent L2 response-cache lookups."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.application.agent_search_cache import AgentSearchResultCache


class BlockingSharedRead:
    """Model an L2 backend read that stays open while all requesters arrive."""

    def __init__(self) -> None:
        self.read_count = 0
        self.write_count = 0

    def get(self, key: str, *, index_version: int) -> bytes | None:
        self.read_count += 1
        _l2_first_read_opened.set()
        _l2_first_read_release.wait(timeout=1)
        return None

    def store(
        self,
        key: str,
        payload: bytes,
        *,
        index_version: int,
        ttl_seconds: float,
    ) -> bool:
        self.write_count += 1
        return True


_l2_first_read_opened = threading.Event()
_l2_first_read_release = threading.Event()


def cache() -> AgentSearchResultCache:
    return AgentSearchResultCache(
        index_version_provider=lambda: 7,
        max_entries=10,
        ttl_seconds=30.0,
    )


async def _receive_all(receive: Callable[[], Any]) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks)


def test_concurrent_identical_searches_perform_one_l2_lookup() -> None:
    from starlette.responses import JSONResponse

    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
    )
    from app.bootstrap.oss_business_routes import SearchRequestOSS

    def search_response() -> dict[str, Any]:
        return {
            "query": "payment risk",
            "status": "ok",
            "top_k": 3,
            "results_count": 1,
            "results": [{"profile_key": "bot:reader:default"}],
            "timing_ms": 1.0,
            "config_applied": {"_retrieval": {"keyword_unavailable": False}},
        }

    class SearchDownstream:
        def __init__(self, result_cache: AgentSearchResultCache) -> None:
            self.calls = 0
            self.result_cache = result_cache

        async def __call__(self, scope, receive, send) -> None:
            self.calls += 1
            await _receive_all(receive)
            request = SearchRequestOSS(query="payment risk", top_k=3)
            assert (
                store_v1_search_response(
                    result_cache=self.result_cache,
                    request=request,
                    response=search_response(),
                    metadata={},
                )
                is True
            )
            await JSONResponse(search_response())(scope, receive, send)

    result_cache = cache()
    shared = BlockingSharedRead()
    downstream = SearchDownstream(result_cache)
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared,
    )

    _l2_first_read_opened.clear()
    _l2_first_read_release.clear()

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            tasks = [
                asyncio.create_task(
                    client.post("/api/v1/search", json={"query": "payment risk", "top_k": 3})
                )
                for _ in range(10)
            ]
            assert await asyncio.to_thread(_l2_first_read_opened.wait, 1)
            await asyncio.sleep(0.05)
            _l2_first_read_release.set()
            responses = await asyncio.gather(*tasks)

        assert len(responses) == 10
        for response in responses:
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

    asyncio.run(scenario())
    assert shared.read_count == 1
    assert downstream.calls == 1


def test_concurrent_identical_recommendations_perform_one_l2_lookup() -> None:
    from starlette.responses import JSONResponse

    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
        store_v1_recommendation_response,
    )
    from app.domain.models.bot_recommendation import (
        BotRecommendation,
        BotRecommendationRequest,
        BotRecommendationResponse,
    )

    recommendation = BotRecommendationResponse(
        trace_id="trace-id",
        type="recommend",
        driver_bot_id=None,
        recommendations=[
            BotRecommendation(
                profile_key="bot:reader:default",
                worker_id="bot:reader",
                score=0.9,
            )
        ],
        metadata={},
    )

    class RecommendationDownstream:
        def __init__(self, result_cache: AgentSearchResultCache) -> None:
            self.calls = 0
            self.result_cache = result_cache

        async def __call__(self, scope, receive, send) -> None:
            self.calls += 1
            body = json.loads(await _receive_all(receive))
            request = BotRecommendationRequest.model_validate(body)
            assert (
                store_v1_recommendation_response(
                    result_cache=self.result_cache,
                    request=request,
                    response=recommendation,
                )
                is True
            )
            await JSONResponse(json.loads(recommendation.model_dump_json()))(
                scope, receive, send
            )

    result_cache = cache()
    shared = BlockingSharedRead()
    downstream = RecommendationDownstream(result_cache)
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
        shared_response_cache=shared,
    )

    _l2_first_read_opened.clear()
    _l2_first_read_release.clear()

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            tasks = [
                asyncio.create_task(
                    client.post(
                        "/api/v1/recommend",
                        json={"question": "payment risk", "topK": 3},
                    )
                )
                for _ in range(10)
            ]
            assert await asyncio.to_thread(_l2_first_read_opened.wait, 1)
            await asyncio.sleep(0.05)
            _l2_first_read_release.set()
            responses = await asyncio.gather(*tasks)

        assert len(responses) == 10
        for response in responses:
            assert response.status_code == 200
            assert response.json()["recommendations"][0]["profile_key"] == "bot:reader:default"

    asyncio.run(scenario())
    assert shared.read_count == 1
    assert downstream.calls == 1
