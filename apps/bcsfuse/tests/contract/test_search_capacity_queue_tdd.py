"""TDD for a bounded search admission queue.

The execution capacity remains finite. A small queue absorbs short bursts at the
admission boundary without sending thousands of simultaneous calls to Embedding
and Qdrant or rejecting the first burst.
"""

from __future__ import annotations

import asyncio

from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY

from app.bootstrap.search_capacity import (
    SearchCapacityMiddleware,
    SearchCapacitySettings,
)


class HoldApp:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = 0

    async def __call__(self, scope, receive, send) -> None:
        self.started += 1
        while True:
            message = await receive()
            if message["type"] in {"http.disconnect", "http.request"}:
                if message["type"] == "http.disconnect" or not message.get(
                    "more_body", False
                ):
                    break
            else:
                continue
        await self.release.wait()
        response = JSONResponse(status_code=200, content={"ok": True})
        await response(scope, receive, send)


class SimpleReceive:
    def __init__(self) -> None:
        self.first = True

    async def __call__(self) -> dict:
        if self.first:
            self.first = False
            return {
                "type": "http.request",
                "body": b'{"query":"assistant"}',
                "more_body": False,
            }
        return {"type": "http.disconnect"}


def search_scope() -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/v2/search",
        "headers": [],
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }


def middleware(**settings_kwargs) -> SearchCapacityMiddleware:
    settings = SearchCapacitySettings(
        max_inflight=1,
        retry_after_seconds=1.0,
        **settings_kwargs,
    )
    return SearchCapacityMiddleware(HoldApp(), settings)


async def invoke(target: SearchCapacityMiddleware) -> tuple[int, bytes]:
    started: dict | None = None
    chunks: list[bytes] = []

    async def send(message) -> None:
        nonlocal started
        if message["type"] == "http.response.start":
            started = dict(message)
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await target(search_scope(), SimpleReceive(), send)
    assert started is not None
    return int(started["status"]), b"".join(chunks)


def test_burst_wait_is_queued_until_an_inflight_slot_is_available():
    target = middleware(max_queue=1, queue_wait_seconds=0.5)
    holder = target.app

    async def scenario():
        first = asyncio.create_task(invoke(target))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(invoke(target))
        await asyncio.sleep(0.05)
        second_completed = second.done()
        holder.release.set()
        return await first, await second, second_completed

    first, second, second_completed = asyncio.run(scenario())
    assert first[0] == 200
    assert second[0] == 200
    assert not second_completed


def test_queue_exceeds_its_bounded_budget_and_returns_429():
    target = middleware(max_queue=0)
    holder = target.app

    async def scenario():
        first = asyncio.create_task(invoke(target))
        await asyncio.sleep(0.01)
        second = await invoke(target)
        holder.release.set()
        first_result = await first
        return first_result, second

    first, second = asyncio.run(scenario())
    assert first[0] == 200
    assert second[0] == 429
    value = REGISTRY.get_sample_value(
        "bcsfuse_search_capacity_rejections_total",
        {"operation": "search", "api_version": "v2"},
    )
    assert value is not None and value >= 1


def test_queue_timeout_returns_429_and_does_not_leak_capacity():
    target = middleware(max_queue=1, queue_wait_seconds=0.01)
    holder = target.app

    async def scenario():
        first = asyncio.create_task(invoke(target))
        await asyncio.sleep(0.01)
        second_future = asyncio.create_task(invoke(target))
        assert await second_future

        holder.release.set()
        first_result = await first
        later = await invoke(target)
        return first_result, second_future.result(), later

    first, timed_out, later = asyncio.run(scenario())
    assert first[0] == 200
    assert timed_out[0] == 429
    assert later[0] == 200


def test_queue_wait_metric_records_time_waiting_for_the_execution_slot():
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

    registry = CollectorRegistry()
    labels = ["operation", "api_version"]
    metrics = {
        "requests": Counter(
            "queue_metric_requests", "requests", [*labels, "outcome"], registry=registry
        ),
        "rejections": Counter(
            "queue_metric_rejections", "rejections", labels, registry=registry
        ),
        "inflight": Gauge(
            "queue_metric_inflight", "inflight", labels, registry=registry
        ),
        "capacity": Gauge(
            "queue_metric_capacity", "capacity", registry=registry
        ),
        "too_large_requests": Counter(
            "queue_metric_too_large", "too large", labels, registry=registry
        ),
        "body_read_errors": Counter(
            "queue_metric_body_errors", "body errors", labels, registry=registry
        ),
        "duration": Histogram(
            "queue_metric_duration", "duration", [*labels, "outcome"], registry=registry
        ),
        "queued": Counter(
            "queue_metric_queued", "queued", labels, registry=registry
        ),
        "queue_wait": Histogram(
            "queue_metric_queue_wait",
            "queue wait",
            labels,
            buckets=(0.025, 0.05, 0.1, 0.25, 0.5),
            registry=registry,
        ),
        "queue_capacity": Gauge(
            "queue_metric_queue_capacity", "queue capacity", registry=registry
        ),
    }
    settings = SearchCapacitySettings(
        max_inflight=1,
        retry_after_seconds=1.0,
        max_queue=1,
        queue_wait_seconds=1.0,
    )
    target = SearchCapacityMiddleware(
        BlockThenRunApp(block_seconds=0.08), settings, metrics=metrics
    )

    async def scenario():
        first = asyncio.create_task(invoke(target))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(invoke(target))
        return await first, await second

    asyncio.run(scenario())
    wait_sum = registry.get_sample_value(
        "queue_metric_queue_wait_sum",
        {"operation": "search", "api_version": "v2"},
    )
    assert wait_sum is not None
    assert wait_sum >= 0.025


class BlockThenRunApp:
    def __init__(self, block_seconds: float) -> None:
        self.block_seconds = block_seconds

    async def __call__(self, scope, receive, send) -> None:
        await asyncio.sleep(self.block_seconds)
        await SimpleReceive()()
        response = JSONResponse(status_code=200, content={"ok": True})
        await response(scope, receive, send)


def test_default_admission_capacity_absorbs_hundred_request_bursts() -> None:
    class EmptyConfig:
        def get(self, key, default=None):
            return default

    class EmptyContext:
        config = EmptyConfig()

    settings = SearchCapacitySettings.from_context(EmptyContext())

    assert settings.max_inflight == 128
    assert settings.max_queue == 128
    assert settings.queue_wait_seconds == 2.0
