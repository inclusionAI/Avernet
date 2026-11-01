"""TDD contract for v1 recommendation response pre-admission caching."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient

from app.application.agent_search_cache import AgentSearchResultCache
from app.domain.models.bot_recommendation import (
    BotRecommendationRequest,
    BotRecommendationResponse,
    BotRecommendation,
)
from app.infra.trace_context import bind_trace_id


def cache() -> AgentSearchResultCache:
    return AgentSearchResultCache(
        index_version_provider=lambda: 7,
        max_entries=10,
        ttl_seconds=30.0,
    )


def request() -> BotRecommendationRequest:
    return BotRecommendationRequest(
        question=" 支付 风险 ",
        topK=3,
        enable_rerank=False,
        filters={"worker_id": "bot:reader"},
    )


def response(trace_id: str = "trace-stored") -> BotRecommendationResponse:
    return BotRecommendationResponse(
        trace_id=trace_id,
        type="recommend",
        driver_bot_id="bot:reader:default",
        recommendations=[
            BotRecommendation(
                profile_key="bot:reader:default",
                worker_id="bot:reader",
                score=0.9,
            )
        ],
        metadata={"cache_hit": False, "expand_factor": 3},
    )


class ShouldNotRun:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        raise AssertionError("cache hit must bypass the downstream ASGI app")


class Downstream:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        payload = {
            "downstream": True,
            "body": json.loads(b"".join(chunks)),
        }
        from starlette.responses import JSONResponse

        await JSONResponse(payload)(scope, receive, send)


def test_preflight_hit_answers_before_admission_and_rewrites_trace_id() -> None:
    from app.adapters.http.recommend_response_cache import (
        build_v1_recommendation_response_cache_key,
        RecommendationResponseCacheMiddleware,
        store_v1_recommendation_response,
    )

    result_cache = cache()
    downstream = ShouldNotRun()
    store_v1_recommendation_response(
        result_cache=result_cache,
        request=request(),
        response=response(),
    )

    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            with bind_trace_id("trace-hit"):
                result = await client.post(
                    "/api/v1/recommend",
                    json={
                        "question": " 支付 风险 ",
                        "topK": 3,
                        "enable_rerank": False,
                        "filters": {"worker_id": "bot:reader"},
                    },
                )

        assert result.status_code == 200
        assert result.json()["trace_id"] == "trace-hit"
        assert result.json()["recommendations"][0]["profile_key"] == "bot:reader:default"
        assert downstream.calls == 0
        assert result_cache.stats["hits"] == 1

    asyncio.run(scenario())


def test_preflight_hit_bypasses_search_capacity_admission() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
        store_v1_recommendation_response,
    )
    from app.bootstrap.search_capacity import (
        SearchCapacityMiddleware,
        SearchCapacitySettings,
    )

    result_cache = cache()
    downstream = ShouldNotRun()
    store_v1_recommendation_response(
        result_cache=result_cache,
        request=request(),
        response=response(),
    )
    capacity = SearchCapacityMiddleware(
        downstream,
        settings=SearchCapacitySettings(max_inflight=1, retry_after_seconds=1.0),
    )
    middleware = RecommendationResponseCacheMiddleware(
        app=capacity,
        result_cache=result_cache,
        require_auth=lambda request: None,
        trace_id_provider=lambda: "trace-capacity-bypass",
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/recommend",
                json={
                    "question": " 支付 风险 ",
                    "topK": 3,
                    "enable_rerank": False,
                    "filters": {"worker_id": "bot:reader"},
                },
            )

        assert result.status_code == 200
        assert result.json()["trace_id"] == "trace-capacity-bypass"
        assert downstream.calls == 0
        assert result_cache.stats["hits"] == 1

    asyncio.run(scenario())


def test_preflight_auth_rejects_cached_payloads_before_sending() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
        store_v1_recommendation_response,
    )

    result_cache = cache()
    downstream = ShouldNotRun()
    store_v1_recommendation_response(
        result_cache=result_cache,
        request=request(),
        response=response(),
    )

    def reject(request: Request) -> None:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
        )

    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=reject,
    )

    labels = {"operation": "recommend", "api_version": "v1", "outcome": "401"}

    def request_metric() -> float:
        from prometheus_client import REGISTRY

        value = REGISTRY.get_sample_value(
            "bcsfuse_search_duration_seconds_count", labels
        )
        return 0.0 if value is None else float(value)

    rejected_before = request_metric()

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/recommend",
                json={"question": "支付", "topK": 3, "enable_rerank": False},
            )

        assert result.status_code == 401
        assert result.json()["detail"]["code"] == "UNAUTHORIZED"
        assert downstream.calls == 0
        assert result_cache.stats["hits"] == 0
        assert request_metric() - rejected_before == 1

    asyncio.run(scenario())


def test_oversized_undeclared_body_keeps_capacity_boundary() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )
    from app.bootstrap.search_capacity import (
        MAX_SEARCH_BODY_BYTES,
        SearchCapacityMiddleware,
        SearchCapacitySettings,
    )

    capacity = SearchCapacityMiddleware(
        ShouldNotRun(),
        settings=SearchCapacitySettings(max_inflight=1, retry_after_seconds=1.0),
    )
    middleware = RecommendationResponseCacheMiddleware(
        app=capacity,
        result_cache=cache(),
        require_auth=lambda request: None,
    )
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/recommend",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
    }
    body = [b"x" * (MAX_SEARCH_BODY_BYTES + 1)]
    finished = False

    async def receive() -> dict[str, Any]:
        nonlocal finished
        if not finished:
            finished = True
            return {"type": "http.request", "body": body[0], "more_body": True}
        return {"type": "http.disconnect"}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def scenario() -> None:
        await middleware(scope, receive, send)

    asyncio.run(scenario())
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    assert start is not None
    assert start["status"] == 413


def test_index_version_or_group_context_misses_replay_the_body_downstream() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
        store_v1_recommendation_response,
    )

    versions = iter([7, 8])

    class Versions:
        def __call__(self) -> int:
            return next(versions)

    result_cache = AgentSearchResultCache(
        index_version_provider=Versions(),
        max_entries=10,
        ttl_seconds=30.0,
    )
    store_v1_recommendation_response(
        result_cache=result_cache,
        request=request(),
        response=response(),
    )
    downstream = Downstream()

    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            version_changed = await client.post(
                "/api/v1/recommend",
                json={
                    "question": " 支付 风险 ",
                    "topK": 3,
                    "enable_rerank": False,
                    "filters": {"worker_id": "bot:reader"},
                },
            )
            group_context = await client.post(
                "/api/v1/recommend",
                json={
                    "question": "支付",
                    "topK": 3,
                    "group_id": "grp_test",
                    "enable_rerank": False,
                },
            )

        assert version_changed.status_code == 200
        assert version_changed.json()["downstream"] is True
        assert version_changed.json()["body"]["topK"] == 3

        assert group_context.status_code == 200
        assert group_context.json()["downstream"] is True
        assert group_context.json()["body"]["group_id"] == "grp_test"
        assert downstream.calls == 2

    asyncio.run(scenario())


def test_degraded_recommendation_responses_are_never_stored() -> None:
    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
        store_v1_recommendation_response,
    )

    result_cache = cache()
    degraded = response()
    degraded.metadata["semantic_unavailable"] = True

    assert (
        store_v1_recommendation_response(
            result_cache=result_cache,
            request=request(),
            response=degraded,
        )
        is False
    )
    assert result_cache.stats["entries"] == 0


def test_cold_identical_recommendations_are_coalesced_before_admission() -> None:
    """Only one identical cold recommendation executes downstream."""
    from starlette.responses import JSONResponse

    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
        store_v1_recommendation_response,
    )

    result_cache = cache()
    entered = asyncio.Event()
    released = asyncio.Event()

    class CoalescingDownstream:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            entered.set()
            await released.wait()
            assert (
                store_v1_recommendation_response(
                    result_cache=result_cache,
                    request=request(),
                    response=response(),
                )
                is True
            )
            await JSONResponse(response("trace-first").model_dump())(
                scope, receive, send
            )

    downstream = CoalescingDownstream()
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            with bind_trace_id("trace-coalesced"):
                tasks = [
                    asyncio.create_task(
                        client.post(
                            "/api/v1/recommend",
                            json={
                                "question": "支付 风险",
                                "topK": 3,
                                "enable_rerank": False,
                                "filters": {"worker_id": "bot:reader"},
                            },
                        )
                    )
                    for index in range(2)
                ]
                await asyncio.wait_for(entered.wait(), timeout=1)
                await asyncio.sleep(0.05)
                assert downstream.calls == 1
                released.set()
                results = await asyncio.gather(*tasks)

        assert all(result.status_code == 200 for result in results)
        assert all(
            result.json()["recommendations"][0]["profile_key"]
            == "bot:reader:default"
            for result in results
        )
        assert downstream.calls == 1

    asyncio.run(scenario())


def test_invalid_json_recommendation_miss_replays_the_body_downstream() -> None:
    """Malformed recommendation JSON belongs to the downstream parser."""
    from starlette.responses import JSONResponse

    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )

    class RawDownstream:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    continue
                if not message.get("more_body", False):
                    break
            await JSONResponse(status_code=400, content={"downstream": True})(
                scope, receive, send
            )

    downstream = RawDownstream()
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=cache(),
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/recommend",
                content=b"",
                headers={"content-type": "application/json"},
            )

        assert result.status_code == 400
        assert result.json()["downstream"] is True
        assert downstream.calls == 1

    asyncio.run(scenario())


def test_degraded_cold_recommendation_burst_is_not_split() -> None:
    """A failure burst for one request produces one core recommendation call."""
    from starlette.responses import JSONResponse

    from app.adapters.http.recommend_response_cache import (
        RecommendationResponseCacheMiddleware,
    )

    result_cache = cache()
    completed = asyncio.Event()

    class BurstDownstream:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            await asyncio.sleep(0.05)
            degraded = response()
            degraded.metadata["semantic_unavailable"] = True
            await JSONResponse(degraded.model_dump())(scope, receive, send)
            completed.set()

    downstream = BurstDownstream()
    middleware = RecommendationResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            requests = [
                asyncio.create_task(
                    client.post(
                        "/api/v1/recommend",
                        json={
                            "question": "duplicate recommendation outage",
                            "topK": 3,
                            "enable_rerank": False,
                        },
                    )
                )
                for _ in range(64)
            ]
            responses = await asyncio.gather(*requests)

        assert [result.status_code for result in responses] == [200] * 64
        assert all(
            result.json()["metadata"]["semantic_unavailable"] is True
            for result in responses
        )
        assert downstream.calls == 1
        assert completed.is_set()

    asyncio.run(scenario())
