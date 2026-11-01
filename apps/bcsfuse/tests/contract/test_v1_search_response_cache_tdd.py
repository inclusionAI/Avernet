"""TDD contract for v1 search response pre-admission caching."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient

from app.application.agent_search_cache import AgentSearchResultCache


def cache() -> AgentSearchResultCache:
    return AgentSearchResultCache(
        index_version_provider=lambda: 7,
        max_entries=10,
        ttl_seconds=30.0,
    )


def search_request() -> Any:
    from app.bootstrap.oss_business_routes import SearchRequestOSS

    return SearchRequestOSS(query=" 支付 风险 ", top_k=3)


def search_response() -> dict[str, Any]:
    return {
        "query": " 支付 风险 ",
        "top_k": 3,
        "mode": "auto",
        "results_count": 1,
        "results": [
            {"profile_key": "bot:reader:default", "score": 0.9}
        ],
        "timing_ms": 1.2,
        "config_applied": {
            "reranker_model": None,
            "filters": {
                "availability": ["protected", "public"],
                "runtime_state": ["online"],
            },
        },
    }


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
        from starlette.responses import JSONResponse

        await JSONResponse(
            {"downstream": True, "body": json.loads(b"".join(chunks))}
        )(scope, receive, send)


def test_preflight_hit_answers_before_admission_and_preserves_projection() -> None:
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
    )
    from app.bootstrap.search_capacity import (
        SearchCapacityMiddleware,
        SearchCapacitySettings,
    )

    result_cache = cache()
    downstream = ShouldNotRun()
    stored = store_v1_search_response(
        result_cache=result_cache,
        request=search_request(),
        response=search_response(),
        metadata={},
    )
    assert stored is True

    capacity = SearchCapacityMiddleware(
        downstream,
        settings=SearchCapacitySettings(max_inflight=1, retry_after_seconds=1.0),
    )
    middleware = SearchResponseCacheMiddleware(
        app=capacity,
        result_cache=result_cache,
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/search",
                json={"query": " 支付 风险 ", "top_k": 3},
            )

        assert result.status_code == 200
        assert result.json()["results"][0]["profile_key"] == "bot:reader:default"
        assert downstream.calls == 0
        assert result_cache.stats["hits"] == 1

    asyncio.run(scenario())


def test_preflight_auth_rejects_cached_payload_and_records_metric() -> None:
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
    )
    from prometheus_client import REGISTRY

    result_cache = cache()
    downstream = ShouldNotRun()
    assert (
        store_v1_search_response(
            result_cache=result_cache,
            request=search_request(),
            response=search_response(),
            metadata={},
        )
        is True
    )

    def reject(request: Request) -> None:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
        )

    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=reject,
    )

    labels = {"operation": "search", "api_version": "v1", "outcome": "401"}
    value = REGISTRY.get_sample_value(
        "bcsfuse_search_duration_seconds_count", labels
    )
    rejected_before = 0.0 if value is None else float(value)

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/search", json={"query": "支付", "top_k": 3}
            )

        assert result.status_code == 401
        assert result.json()["detail"]["code"] == "UNAUTHORIZED"
        assert downstream.calls == 0
        assert result_cache.stats["hits"] == 0
        new_value = REGISTRY.get_sample_value(
            "bcsfuse_search_duration_seconds_count", labels
        )
        assert float(new_value or 0) - rejected_before == 1

    asyncio.run(scenario())


def test_index_version_miss_replays_the_body_downstream() -> None:
    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
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
    assert (
        store_v1_search_response(
            result_cache=result_cache,
            request=search_request(),
            response=search_response(),
            metadata={},
        )
        is True
    )
    downstream = Downstream()
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/search",
                json={"query": " 支付 风险 ", "top_k": 3},
            )

        assert result.status_code == 200
        assert result.json()["downstream"] is True
        assert result.json()["body"]["top_k"] == 3
        assert downstream.calls == 1

    asyncio.run(scenario())


def test_degraded_search_results_are_never_stored() -> None:
    from app.adapters.http.search_response_cache import (
        store_v1_search_response,
    )

    result_cache = cache()
    assert (
        store_v1_search_response(
            result_cache=result_cache,
            request=search_request(),
            response=search_response(),
            metadata={
                "semantic_unavailable": True,
                "keyword_unavailable": False,
            },
        )
        is False
    )
    assert (
        store_v1_search_response(
            result_cache=result_cache,
            request=search_request(),
            response=search_response(),
            metadata={"score_source": "regex_fallback"},
        )
        is False
    )
    assert result_cache.stats["entries"] == 0


def test_cold_identical_requests_are_coalesced_before_admission() -> None:
    """Only one identical cold request reaches downstream projection."""
    from starlette.responses import JSONResponse

    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
    )

    result_cache = cache()
    released = asyncio.Event()
    entered = asyncio.Event()

    class CoalescingDownstream:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            entered.set()
            await released.wait()
            assert (
                store_v1_search_response(
                    result_cache=result_cache,
                    request=search_request(),
                    response=search_response(),
                    metadata={},
                )
                is True
            )
            await JSONResponse(search_response())(scope, receive, send)

    downstream = CoalescingDownstream()
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=result_cache,
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/api/v1/search",
                    json={"query": " 支付 风险 ", "top_k": 3},
                )
            )
            second = asyncio.create_task(
                client.post(
                    "/api/v1/search",
                    json={"query": "支付 风险", "top_k": 3},
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            await asyncio.sleep(0.05)
            assert downstream.calls == 1
            released.set()
            first_result, second_result = await asyncio.gather(first, second)

        assert first_result.status_code == 200
        assert second_result.status_code == 200
        assert (
            first_result.json()["results"][0]["profile_key"]
            == "bot:reader:default"
        )
        assert (
            second_result.json()["results"][0]["profile_key"]
            == "bot:reader:default"
        )
        assert downstream.calls == 1

    asyncio.run(scenario())


def test_invalid_json_miss_replays_the_body_downstream() -> None:
    """Malformed JSON is not interpreted or swallowed by cache preflight."""
    from starlette.responses import JSONResponse

    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
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
    middleware = SearchResponseCacheMiddleware(
        app=downstream,
        result_cache=cache(),
        require_auth=lambda request: None,
    )

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=middleware), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/v1/search", content=b"", headers={"content-type": "application/json"}
            )

        assert result.status_code == 400
        assert result.json()["downstream"] is True
        assert downstream.calls == 1

    asyncio.run(scenario())


def test_cold_identical_request_burst_does_not_split_singleflight_locks() -> None:
    """A burst of identical misses must share one downstream execution."""
    from starlette.responses import JSONResponse

    from app.adapters.http.search_response_cache import (
        SearchResponseCacheMiddleware,
        store_v1_search_response,
    )

    result_cache = cache()
    entered = asyncio.Event()
    release = asyncio.Event()

    class BurstDownstream:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            entered.set()
            await release.wait()
            assert (
                store_v1_search_response(
                    result_cache=result_cache,
                    request=search_request(),
                    response=search_response(),
                    metadata={},
                )
                is True
            )
            await JSONResponse(search_response())(scope, receive, send)

    downstream = BurstDownstream()
    middleware = SearchResponseCacheMiddleware(
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
                        "/api/v1/search",
                        json={"query": "支付 风险", "top_k": 3},
                    )
                )
                for _ in range(64)
            ]
            await asyncio.wait_for(entered.wait(), timeout=1)
            await asyncio.sleep(0.1)
            release.set()
            responses = await asyncio.gather(*requests)

        assert [response.status_code for response in responses] == [200] * 64
        assert downstream.calls == 1

    asyncio.run(scenario())


def test_degraded_cold_burst_is_not_split_into_duplicate_downstream_calls() -> None:
    """An outage at a single query must produce one expensive retrieval call."""
    from starlette.responses import JSONResponse

    from app.adapters.http.search_response_cache import SearchResponseCacheMiddleware

    result_cache = cache()
    completed = asyncio.Event()

    class BurstDownstream:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            # Move other middleware coroutines to their finish handlers before
            # the first request releases its lock.
            await asyncio.sleep(0.05)
            await JSONResponse({"results": [], "status": "degraded"})(
                scope, receive, send
            )
            completed.set()

    downstream = BurstDownstream()
    middleware = SearchResponseCacheMiddleware(
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
                        "/api/v1/search",
                        json={"query": "duplicate outage query", "top_k": 3},
                    )
                )
                for _ in range(64)
            ]
            responses = await asyncio.gather(*requests)

        assert [response.status_code for response in responses] == [200] * 64
        assert downstream.calls == 1
        assert completed.is_set()

    asyncio.run(scenario())
