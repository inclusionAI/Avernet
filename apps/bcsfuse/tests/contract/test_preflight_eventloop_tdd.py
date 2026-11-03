"""TDD for keeping the HTTP event loop responsive during request preflight."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.application.agent_search_cache import AgentSearchResultCache


def result_cache() -> AgentSearchResultCache:
    return AgentSearchResultCache(
        index_version_provider=lambda: 17,
        max_entries=10,
        ttl_seconds=30.0,
    )


class Downstream:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.calls.append(scope["path"])
        if scope["path"] == "/health":
            await JSONResponse({"ok": True})(scope, receive, send)
            return
        # Consume the request body and return a small success response.
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            if not message.get("more_body", False):
                break
        await JSONResponse({"downstream": True})(scope, receive, send)


def test_recommend_preflight_parsing_does_not_hold_the_event_loop(monkeypatch):
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )

    original_loads = __import__("json").loads

    def slow_loads(payload):  # type: ignore[no-untyped-def]
        time.sleep(0.05)
        return original_loads(payload)

    monkeypatch.setattr(
        "app.adapters.http.recommend_response_cache.json.loads",
        slow_loads,
    )
    downstream = Downstream()
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache(),
        require_auth=lambda request: None,
    )

    async def scenario() -> float:
        transport = ASGITransport(app=middleware)
        started = time.perf_counter()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            recommend = asyncio.create_task(
                client.post("/api/v1/recommend", json={"question": "semantic"})
            )
            await asyncio.sleep(0.005)
            health_started = time.perf_counter()
            health = await client.get("/health")
            health_duration = time.perf_counter() - health_started
            recommendation = await recommend
        assert health.status_code == 200
        assert recommendation.status_code == 200
        return health_duration

    health_duration = asyncio.run(scenario())
    assert health_duration < 0.03
    assert downstream.calls == ["/health", "/api/v1/recommend"]


def test_preflight_publishes_intent_route_without_capacity_reparsing():
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )

    downstream = Downstream()
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache(),
        require_auth=lambda request: None,
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/recommend",
        "query_string": b"",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
    }

    async def receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"question":"find worker bot:reader:default"}',
            "more_body": False,
        }

    async def send(message: dict[str, Any]) -> None:
        return None

    asyncio.run(middleware(scope, receive, send))
    assert scope["state"]["bcsfuse_search_route"] == "direct_keyword"



def test_capacity_uses_preflight_route_instead_of_reparsing_body() -> None:
    from app.bootstrap.search_capacity import (
        SearchCapacityMiddleware,
        SearchCapacitySettings,
    )

    class HoldApp:
        def __init__(self) -> None:
            self.release = asyncio.Event()
            self.started = 0

        async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
            self.started += 1
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    continue
                if not message.get("more_body", False):
                    break
            await self.release.wait()
            await JSONResponse({"held": True})(scope, receive, send)

    target = SearchCapacityMiddleware(
        HoldApp(),
        settings=SearchCapacitySettings(
            max_inflight=1,
            max_queue=0,
            retry_after_seconds=1.0,
            direct_keyword_max_inflight=8,
            direct_keyword_max_queue=0,
        ),
    )
    downstream = target.app

    def receive() -> Any:
        async def call() -> dict[str, Any]:
            return {
                "type": "http.request",
                "body": b'{"question":"unambiguously semantic prose"}',
                "more_body": False,
            }
        return call

    def scope() -> dict[str, Any]:
        return {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/recommend",
            "query_string": b"",
            "headers": [],
            "http_version": "1.1",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "state": {"bcsfuse_search_route": "direct_keyword"},
        }

    async def invoke() -> int:
        statuses: list[int] = []

        async def recording_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                statuses.append(int(message["status"]))

        await target(scope(), receive(), recording_send)
        assert statuses
        return statuses[0]

    async def scenario() -> list[int]:
        first = asyncio.create_task(invoke())
        second = asyncio.create_task(invoke())
        deadline = time.monotonic() + 1.0
        while downstream.started < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert downstream.started == 2
        downstream.release.set()
        return await asyncio.gather(first, second)

    statuses = asyncio.run(scenario())
    assert statuses == [200, 200]
