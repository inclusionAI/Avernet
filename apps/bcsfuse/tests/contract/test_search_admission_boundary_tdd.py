"""TDD for body-reading failures and capacity admission isolation."""

from __future__ import annotations

import asyncio

from fastapi.responses import JSONResponse
from prometheus_client import REGISTRY

from app.bootstrap.search_capacity import (
    SearchCapacityMiddleware,
    SearchCapacitySettings,
)


class OKApp:
    async def __call__(self, scope, receive, send) -> None:
        # Consume the replayed request body before returning the stable test response.
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            if message["type"] == "http.request" and not message.get("more_body", False):
                break
        response = JSONResponse(status_code=200, content={"ok": True})
        await response(scope, receive, send)


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


def middleware(max_inflight: int = 1) -> SearchCapacityMiddleware:
    settings = SearchCapacitySettings(
        max_inflight=max_inflight,
        retry_after_seconds=1.0,
    )
    return SearchCapacityMiddleware(OKApp(), settings)


async def invoke(target: SearchCapacityMiddleware, receive) -> tuple[int, bytes]:
    started: dict | None = None
    chunks: list[bytes] = []

    async def send(message) -> None:
        nonlocal started
        if message["type"] == "http.response.start":
            started = dict(message)
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    try:
        await target(search_scope(), receive, send)
    finally:
        assert started is not None
        # Note: this is an explicit test reader for the ASGI message contract.
        status_code = int(started["status"])
    return status_code, b"".join(chunks)


class SimpleReceive:
    async def __call__(self) -> dict:
        return {
            "type": "http.request",
            "body": b'{"query":"assistant semantic search"}',
            "more_body": False,
        }


class HangingReceive:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def __call__(self) -> dict:
        if not getattr(self, "first_chunk_read", False):
            self.first_chunk_read = True
            return {
                "type": "http.request",
                "body": b'{"query":"slow-upload"}',
                "more_body": True,
            }
        await self.release.wait()
        return {"type": "http.request", "body": b"", "more_body": False}


class FailingReceive:
    async def __call__(self) -> dict:
        if not getattr(self, "first_chunk_read", False):
            self.first_chunk_read = True
            return {
                "type": "http.request",
                "body": b'{"query":"assistant semantic search"}',
                "more_body": True,
            }
        raise RuntimeError("request body transport failed")


class ReceiveThenDisconnect:
    async def __call__(self) -> dict:
        if not getattr(self, "first_chunk_read", False):
            self.first_chunk_read = True
            return {
                "type": "http.request",
                "body": b'{"query":"assistant semantic search"}',
                "more_body": True,
            }
        return {"type": "http.disconnect"}


def test_body_upload_does_not_hold_the_only_admission_slot() -> None:
    target = middleware(max_inflight=1)
    slow_body = HangingReceive()

    async def scenario():
        first = asyncio.create_task(invoke(target, slow_body))
        await asyncio.sleep(0.01)
        second = await invoke(target, SimpleReceive())
        slow_body.release.set()
        first_status, _ = await first
        return second[0], first_status

    second_status, first_status = asyncio.run(scenario())
    assert first_status == 200
    assert second_status == 200


def test_receive_failure_does_not_leak_the_only_admission_slot() -> None:
    target = middleware(max_inflight=1)

    async def scenario():
        before = REGISTRY.get_sample_value(
            "bcsfuse_search_body_read_errors_total",
            {"operation": "search", "api_version": "v2"},
        ) or 0
        invalid_state = False
        try:
            await invoke(target, FailingReceive())
        except Exception:
            invalid_state = True
        after = REGISTRY.get_sample_value(
            "bcsfuse_search_body_read_errors_total",
            {"operation": "search", "api_version": "v2"},
        ) or 0
        second = await invoke(target, SimpleReceive())
        return invalid_state, second, after - before

    invalid_state, second, errors = asyncio.run(scenario())
    assert invalid_state is True
    assert second[0] == 200
    assert errors >= 1


def test_disconnect_while_reading_body_does_not_leak_admission() -> None:
    target = middleware(max_inflight=1)

    async def scenario():
        first = await invoke(target, ReceiveThenDisconnect())
        second = await invoke(target, SimpleReceive())
        return first[0], second[0]

    first_status, second_status = asyncio.run(scenario())
    assert first_status == 200
    assert second_status == 200
