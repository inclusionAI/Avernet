"""TDD for circuit-aware search admission routing."""

from __future__ import annotations

import asyncio
import json

from fastapi.responses import JSONResponse

from app.application.services.retrieval_dependency_circuit import (
    RetrievalDependencyCircuit,
)
from app.bootstrap.search_capacity import (
    SearchCapacityMiddleware,
    SearchCapacitySettings,
)


class HoldApp:
    def __init__(self, name: str) -> None:
        self.name = name
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, scope, receive, send) -> None:
        self.entered.set()
        await self.release.wait()
        response = JSONResponse(status_code=200, content={"route": self.name})
        await response(scope, receive, send)


class BoundBodyReceive:
    def __init__(self, payload: dict) -> None:
        self.first = True
        self.body = json.dumps(payload).encode()

    async def __call__(self) -> dict:
        if self.first:
            self.first = False
            return {
                "type": "http.request",
                "body": self.body,
                "more_body": False,
            }
        return {"type": "http.disconnect"}


def scope(path: str) -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "query_string": b"",
    }


def settings() -> SearchCapacitySettings:
    return SearchCapacitySettings(
        max_inflight=1,
        retry_after_seconds=1.0,
        max_queue=0,
        queue_wait_seconds=1.0,
        direct_keyword_max_inflight=1,
        direct_keyword_max_queue=0,
        direct_keyword_queue_wait_seconds=1.0,
        local_fallback_max_inflight=1,
        local_fallback_max_queue=0,
        local_fallback_queue_wait_seconds=0.5,
    )


async def invoke(target: SearchCapacityMiddleware, path: str, payload: dict) -> dict:
    started: dict[str, int] = {}

    async def send(message) -> None:
        if message["type"] == "http.response.start":
            started["status"] = int(message["status"])

    await target(scope(path), BoundBodyReceive(payload), send)
    return started


def test_open_dual_outage_uses_an_independent_fallback_pool() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30.0)
    semantic = HoldApp("semantic")
    fallback = HoldApp("fallback")
    direct = HoldApp("direct")

    class RoutingApp:
        async def __call__(self, current_scope, receive, send) -> None:
            body = json.dumps({"query": "semantic query"}).encode()
            route = "fallback" if circuit.outaged else "semantic"
            app = {
                "semantic": semantic,
                "fallback": fallback,
                "direct": direct,
            }[route]
            await app(current_scope, receive, send)

    target = SearchCapacityMiddleware(
        RoutingApp(),
        settings(),
        retrieval_dependency_circuit=circuit,
    )

    async def scenario() -> None:
        semantic_request = asyncio.create_task(
            invoke(
                target,
                "/api/v1/search",
                {"query": "semantic query"},
            )
        )
        await asyncio.wait_for(semantic.entered.wait(), timeout=0.2)
        circuit.record_outage()

        fallback_request = asyncio.create_task(
            invoke(
                target,
                "/api/v1/search",
                {"query": "semantic query"},
            )
        )
        await asyncio.wait_for(fallback.entered.wait(), timeout=0.2)
        semantic.release.set()
        fallback.release.set()
        assert await semantic_request == {"status": 200}
        assert await fallback_request == {"status": 200}

    asyncio.run(scenario())


def test_closed_circuit_still_uses_the_semantic_pool() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30.0)
    semantic = HoldApp("semantic")
    fallback = HoldApp("fallback")

    class RoutingApp:
        async def __call__(self, current_scope, receive, send) -> None:
            await ((fallback if circuit.outaged else semantic)(
                current_scope, receive, send
            ))

    target = SearchCapacityMiddleware(
        RoutingApp(),
        settings(),
        retrieval_dependency_circuit=circuit,
    )

    async def scenario() -> None:
        request = asyncio.create_task(
            invoke(target, "/api/v1/search", {"query": "semantic query"})
        )
        await asyncio.wait_for(semantic.entered.wait(), timeout=0.2)
        assert not fallback.entered.is_set()
        semantic.release.set()
        assert await request == {"status": 200}

    asyncio.run(scenario())


def test_local_fallback_capacity_defaults_exceed_the_semantic_slot_budget() -> None:
    class EmptyConfig:
        def get(self, key, default=None):
            return default

    class EmptyContext:
        config = EmptyConfig()

    settings = SearchCapacitySettings.from_context(EmptyContext())

    assert settings.local_fallback_max_inflight >= 512
    assert settings.local_fallback_max_queue >= 512
    assert settings.local_fallback_queue_wait_seconds <= 0.25
