"""Capacity admission and observability for search and recommendation."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry


class Config:
    def __init__(self, values: dict[str, int | float]):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.values.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(key, default))


class Startup:
    async def initialize(self):
        return None

    async def shutdown(self):
        return None


def context(max_inflight: int) -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", Config({
        "search.capacity.max_inflight": max_inflight,
        # This legacy test exercises the queue-exhausted path. With
        # zero queue capacity the second request still fails fast.
        "search.capacity.max_queue": 0,
    }))
    registry.register("secret_provider", object())
    registry.register("startup_provider", Startup())
    return ApplicationContext(
        mode="test", startup_profile="opensource", registry=registry
    )


def search_router(blocked: asyncio.Event) -> APIRouter:
    router = APIRouter(prefix="/api/v2")

    @router.post("/search")
    async def search():
        await blocked.wait()
        return {"status": "ok"}

    @router.post("/recommend")
    async def recommend():
        await blocked.wait()
        return {"status": "ok"}

    return router


def sample(name: str, labels: dict[str, str] | None = None) -> float:
    value = REGISTRY.get_sample_value(name, labels or {})
    return 0.0 if value is None else float(value)


async def exercise(max_inflight: int):
    blocked = asyncio.Event()
    app = create_bcsfuse_app(
        context(max_inflight), search_router=search_router(blocked)
    )
    accepted_before = sample(
        "bcsfuse_search_duration_seconds_count",
        {"operation": "search", "api_version": "v2", "outcome": "200"},
    )
    overloaded_before = sample(
        "bcsfuse_search_duration_seconds_count",
        {"operation": "recommend", "api_version": "v2", "outcome": "429"},
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = asyncio.create_task(
            client.post("/api/v2/search", json={"query": "assistant semantic search"})
        )
        await asyncio.sleep(0.01)
        second = asyncio.create_task(
            client.post("/api/v2/recommend", json={"question": "assistant semantic search"})
        )
        blocked.set()
        accepted, overloaded = await first, await second
        metrics_response = await client.get("/metrics")

    return {
        "accepted": accepted,
        "overloaded": overloaded,
        "accepted_histogram_count": sample(
            "bcsfuse_search_duration_seconds_count",
            {"operation": "search", "api_version": "v2", "outcome": "200"},
        )
        - accepted_before,
        "overloaded_histogram_count": sample(
            "bcsfuse_search_duration_seconds_count",
            {"operation": "recommend", "api_version": "v2", "outcome": "429"},
        )
        - overloaded_before,
        "metrics_text": metrics_response.text,
        "overload_total": sample(
            "bcsfuse_search_capacity_rejections_total",
            {"operation": "recommend", "api_version": "v2"},
        ),
        "inflight": sample(
            "bcsfuse_search_requests_inflight",
            {"operation": "search", "api_version": "v2"},
        ),
        "capacity": sample("bcsfuse_search_capacity_max_inflight"),
    }


def test_overloaded_request_fails_fast_with_429_and_metrics():
    result = asyncio.run(exercise(max_inflight=1))

    assert result["accepted"].status_code == 200
    assert result["overloaded"].status_code == 429
    assert result["overloaded"].headers["retry-after"] == "1"
    assert result["overloaded"].json()["detail"]["code"] == "SEARCH_CAPACITY_EXCEEDED"
    assert result["accepted_histogram_count"] == 1
    assert result["overloaded_histogram_count"] == 1
    assert int(result["overload_total"]) >= 1
    assert result["inflight"] == 0
    assert result["capacity"] == 1
    assert "bcsfuse_search_capacity_rejections_total" in result["metrics_text"]
    assert "bcsfuse_search_duration_seconds" in result["metrics_text"]


def test_capacity_configuration_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        create_bcsfuse_app(context(0), search_router=search_router(asyncio.Event()))


def test_search_worker_capacity_is_isolated_from_default_threadpool(monkeypatch):
    import anyio.to_thread

    from app.adapters.http.search_threadpool import (
        configure_search_thread_capacity,
        run_search_in_threadpool,
    )

    configure_search_thread_capacity(1)

    async def scenario():
        default_limiter = anyio.to_thread.current_default_thread_limiter()
        original_tokens = default_limiter.total_tokens
        try:
            default_limiter.total_tokens = 1
            async with default_limiter:
                # The only Starlette default worker token is deliberately occupied.
                # An admitted search call must still have its own bounded worker,
                # rather than waiting in a hidden default-threadpool queue.
                return await asyncio.wait_for(
                    run_search_in_threadpool(lambda: "ok"), timeout=1
                )
        finally:
            default_limiter.total_tokens = original_tokens

    try:
        assert asyncio.run(scenario()) == "ok"
    finally:
        monkeypatch.setattr(
            "app.adapters.http.search_threadpool._search_thread_limiter",
            anyio.to_thread.CapacityLimiter(64),
        )


def test_composition_configures_dedicated_search_workers_from_admission_capacity():
    from app.adapters.http.search_threadpool import (
        configure_local_fallback_thread_capacity,
        configure_search_thread_capacity,
        get_local_fallback_thread_capacity,
        get_search_thread_capacity,
    )

    app = create_bcsfuse_app(
        context(37), search_router=search_router(asyncio.Event())
    )
    try:
        assert app.state.search_capacity_max_inflight == 37
        assert get_search_thread_capacity() == 37
        assert get_local_fallback_thread_capacity() == 37
    finally:
        configure_search_thread_capacity(64)
        configure_local_fallback_thread_capacity(64)
