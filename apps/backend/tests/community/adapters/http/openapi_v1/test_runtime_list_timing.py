"""Only slow GET list requests carry the diagnostic timeline."""

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest

from agentclaw.community.adapters.http.openapi_v1 import access_log
from agentclaw.community.plugins import http_client
from agentclaw.community.utils import runtime_list_timing as timing


@pytest.mark.asyncio
async def test_failed_target_restores_context_and_preserves_exception():
    failure = RuntimeError("expected")

    async def fail(scope, receive, send):
        assert timing.current.get() is not None
        for _ in range(300):
            timing.mark("step")
        assert len(timing.current.get().points) == 256
        raise failure

    with pytest.raises(RuntimeError) as raised:
        await access_log.PublicApiAccessLogMiddleware(fail)(
            {"type": "http", "method": "GET", "path": "/openapi/v1/bots/b/models"}, None, None
        )
    assert raised.value is failure
    assert timing.current.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "elapsed", "reported"), [
    ("GET", "/openapi/v1/bots/a/models", .5, True),
    ("GET", "/openapi/v1/bots/catalog/search", .75, True),
    ("GET", "/openapi/v1/bots/a/sessions", .75, True),
    ("GET", "/openapi/v1/bots/a/connection", .75, True),
    ("GET", "/openapi/v1/bots/a/sessions/favorites", .75, True),
    ("GET", "/openapi/v1/bots/a/sessions/private-key/messages", .6, True),
    ("GET", "/openapi/v1/bots/a/sessions", .49, False),
    ("POST", "/openapi/v1/bots/a/sessions", 1, False),
    ("GET", "/openapi/v1/bots/a/models/detail", 1, False),
])
async def test_summary(method, path, elapsed, reported, monkeypatch, caplog):
    now = [0.0]
    monkeypatch.setattr(access_log, "time", SimpleNamespace(perf_counter=lambda: now[0]))
    monkeypatch.setattr(timing, "perf_counter", lambda: now[0])
    async def downstream(scope, receive, send):
        now[0] = .125
        timing.mark("handler")
        assert await timing.timed_thread("device", lambda: 7) == 7
        now[0] = elapsed
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message):
        assert message["status"] == 200

    scope = {"type": "http", "path": path, "method": method, "headers": [], "state": {"trace_id": "case"}}
    with caplog.at_level(logging.INFO):
        await access_log.PublicApiAccessLogMiddleware(downstream)(scope, None, send)
    line = next(r.message for r in caplog.records if r.message.startswith("openapi_access "))
    assert ("phase_offsets_ms=" in line) == reported
    if reported:
        assert "trace_id=case" in line
        assert line.split("phase_offsets_ms=")[1] == (
            "handler:125.0,device.submit:125.0,device.start:125.0,device.done:125.0,device.resume:125.0"
        )
    assert timing.current.get() is None


@pytest.mark.asyncio
async def test_overlapping_workers_keep_failures_and_timelines_separate():
    async def one(label):
        timeline = timing.Timeline(timing.perf_counter())
        token = timing.current.set(timeline)
        error = RuntimeError(label)
        def fail():
            raise error
        try:
            with pytest.raises(RuntimeError) as raised:
                await timing.timed_thread(label, fail)
            assert raised.value is error
            return [p.split(":")[0] for p in timeline.points]
        finally:
            timing.current.reset(token)

    a, b = await asyncio.gather(one("bot"), one("transport"))
    assert a == ["bot.submit", "bot.start", "bot.done", "bot.resume"]
    assert b == ["transport.submit", "transport.start", "transport.done", "transport.resume"]


def test_real_response_hook_preserves_body_and_does_not_record_request_data(monkeypatch):
    original = httpx.Client
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"private": "message"}))
    monkeypatch.setattr(http_client.httpx, "Client", lambda **kw: original(transport=transport, **kw))
    client = http_client.HttpxClient("https://example.test")
    timeline = timing.Timeline(timing.perf_counter())
    token = timing.current.set(timeline)
    try:
        assert client.get("/private-session/messages").json() == {"private": "message"}
        assert [p.split(":")[0] for p in timeline.points] == ["http.request", "http.headers", "http.request_end"]
    finally:
        timing.current.reset(token)
        client.close()
