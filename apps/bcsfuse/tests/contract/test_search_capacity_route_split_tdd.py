"""TDD for semantic and direct-keyword admission isolation."""

from __future__ import annotations

import asyncio
import json

from fastapi.responses import JSONResponse

from app.bootstrap.search_capacity import (
    SearchCapacityMiddleware,
    SearchCapacitySettings,
)


class SeparateHoldApp:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, scope, receive, send) -> None:
        self.entered.set()
        while True:
            message = await receive()
            if message["type"] == "http.request" and not message.get(
                "more_body", False
            ):
                break
        await self.release.wait()
        response = JSONResponse(status_code=200, content={"route_entered": True})
        await response(scope, receive, send)


class BoundBodyReceive:
    def __init__(self, payload: dict) -> None:
        self.first = True
        self.body = json.dumps(payload).encode("utf-8")

    async def __call__(self) -> dict:
        if self.first:
            self.first = False
            return {
                "type": "http.request",
                "body": self.body,
                "more_body": False,
            }
        return {"type": "http.disconnect"}


class TwoRouteApp:
    def __init__(self, *, search, recommendation) -> None:
        self.search = search
        self.recommendation = recommendation

    async def __call__(self, route_scope, receive, send) -> None:
        app = self.search if route_scope["path"] == "/api/v1/search" else self.recommendation
        await app(route_scope, receive, send)


def scope(path: str) -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }


def settings() -> SearchCapacitySettings:
    return SearchCapacitySettings(
        max_inflight=1,
        retry_after_seconds=1.0,
        max_queue=1,
        queue_wait_seconds=1.0,
        direct_keyword_max_inflight=1,
        direct_keyword_max_queue=1,
        direct_keyword_queue_wait_seconds=1.0,
    )


def request(app, path: str, payload: dict) -> SearchCapacityMiddleware:
    target = SearchCapacityMiddleware(app, settings())
    target.scope = scope(path)
    target.receive = BoundBodyReceive(payload)
    return target


async def invoke(target: SearchCapacityMiddleware) -> None:
    started: dict = {}

    async def send(message) -> None:
        if message["type"] == "http.response.start":
            started["status"] = message["status"]

    await target(target.scope, target.receive, send)
    assert started is not None and int(started["status"]) == 200


def test_direct_keyword_admission_is_isolated_from_semantic_admission() -> None:
    direct = SeparateHoldApp()
    semantic = SeparateHoldApp()
    app = TwoRouteApp(search=direct, recommendation=semantic)

    async def scenario() -> None:
        direct_request = asyncio.create_task(
            invoke(request(app, "/api/v1/search", {"query": "bot:reader:default"}))
        )
        semantic_request = asyncio.create_task(
            invoke(
                request(
                    app,
                    "/api/v1/recommend",
                    {"question": "python search engine optimization"},
                )
            )
        )
        await asyncio.wait_for(
            asyncio.gather(direct.entered.wait(), semantic.entered.wait()),
            timeout=0.2,
        )
        direct.release.set()
        semantic.release.set()
        await asyncio.gather(direct_request, semantic_request)

    asyncio.run(scenario())


def test_direct_keyword_pool_defaults_match_hundred_request_burst_budget() -> None:
    class EmptyConfig:
        def get(self, key, default=None):
            return default

    class EmptyContext:
        config = EmptyConfig()

    settings = SearchCapacitySettings.from_context(EmptyContext())

    assert settings.direct_keyword_max_inflight == 128
    assert settings.direct_keyword_max_queue >= 128
    assert settings.direct_keyword_queue_wait_seconds == 2.0
