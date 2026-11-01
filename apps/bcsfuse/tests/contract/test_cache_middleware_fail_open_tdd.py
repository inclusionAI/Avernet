"""TDD: cache middleware failures must fail open to the admitted route."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY

from app.application.agent_search_cache import AgentSearchResultCache


class Downstream:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        from starlette.responses import JSONResponse

        await JSONResponse(
            {
                "downstream": True,
                "body": json.loads(b"".join(chunks)),
            }
        )(scope, receive, send)


class PoisonedResultCache(AgentSearchResultCache):
    def get(self, key: str) -> Any:
        raise RuntimeError("process cache reader failed")


def _cache() -> PoisonedResultCache:
    return PoisonedResultCache(
        index_version_provider=lambda: 7,
        max_entries=10,
        ttl_seconds=30.0,
    )


def _middleware_errors(operation: str) -> float:
    value = REGISTRY.get_sample_value(
        "bcsfuse_search_shared_response_cache_errors_total",
        {"operation": operation, "stage": "preflight"},
    )
    return 0.0 if value is None else float(value)


def test_search_response_cache_failure_fails_open_to_downstream() -> None:
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
    )

    downstream = Downstream()
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=_cache(),
        require_auth=lambda request: None,
    )
    errors_before = _middleware_errors("search")

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/search", json={"query": "python search", "top_k": 3}
            )

        assert response.status_code == 200
        assert response.json()["downstream"] is True

    asyncio.run(scenario())
    assert downstream.calls == 1
    assert _middleware_errors("search") - errors_before == 1


def test_recommend_response_cache_failure_fails_open_to_downstream() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )

    downstream = Downstream()
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=_cache(),
        require_auth=lambda request: None,
    )
    errors_before = _middleware_errors("recommend")

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/recommend",
                json={"question": "python search", "topK": 3, "enable_rerank": False},
            )

        assert response.status_code == 200
        assert response.json()["downstream"] is True

    asyncio.run(scenario())
    assert downstream.calls == 1
    assert _middleware_errors("recommend") - errors_before == 1
