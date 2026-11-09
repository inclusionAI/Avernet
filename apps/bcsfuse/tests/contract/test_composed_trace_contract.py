"""Trace context must survive composition and remain request-local."""

import asyncio
import logging

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from app.bootstrap.opensource_app import create_opensource_app
from app.infra.public.observability.trace_middleware import TraceIdMiddleware
from app.infra.trace_context import get_trace_id, set_trace_id


def test_online_request_logs_server_duration_with_trace(caplog):
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    def background_probe():
        logging.getLogger("app.infra.public.observability.trace_middleware").info(
            "[ONLINE-TIMING] phase=background_probe observed_trace_id=%s", get_trace_id(),
        )

    @app.put("/v1/workers/{worker_id}/online")
    async def online(worker_id: str, background_tasks: BackgroundTasks):
        background_tasks.add_task(background_probe)
        return {"worker_id": worker_id}

    caplog.set_level(logging.INFO, logger="app.infra.public.observability.trace_middleware")
    with TestClient(app) as client:
        response = client.put("/v1/workers/bot-1/online", headers={"X-Trace-ID": "online-probe"})

    assert response.status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    assert any("phase=request_start" in message and "trace_id=online-probe" in message for message in messages)
    assert any("phase=request_end" in message and "status=200" in message and "elapsed_ms=" in message for message in messages)
    response_index = next(index for index, message in enumerate(messages) if "phase=response_sent" in message)
    background_index = next(index for index, message in enumerate(messages) if "phase=background_probe" in message)
    complete_index = next(index for index, message in enumerate(messages) if "phase=request_end" in message)
    assert response_index < background_index < complete_index
    assert "observed_trace_id=online-probe" in messages[background_index]
    assert "post_response_ms=" in messages[complete_index]


@pytest.mark.parametrize("headers,expected", [
    ({"X-Trace-ID": "upstream", "X-Request-ID": "fallback"}, "upstream"),
    ({"X-Request-ID": "fallback"}, "fallback"),
    ({}, None),
])
def test_composed_app_propagates_trace_and_restores_context(monkeypatch, headers, expected):
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    app = create_opensource_app(mode="test")

    @app.get("/_trace_probe")
    async def trace_probe():
        return {"trace_id": get_trace_id()}

    set_trace_id("outside-request")
    try:
        with TestClient(app) as client:
            response = client.get("/_trace_probe", headers=headers)
            trace_id = response.json()["trace_id"]
            assert trace_id == response.headers["X-Trace-ID"]
            if expected:
                assert trace_id == expected
            else:
                assert trace_id.startswith("trace_")
            other = client.get("/_trace_probe")
            assert other.json()["trace_id"] != trace_id
        assert get_trace_id() == "outside-request"
    finally:
        set_trace_id("")


@pytest.mark.asyncio
async def test_trace_isolation_for_concurrent_and_failed_requests():
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/trace/{fail}")
    async def probe(fail: bool):
        before = get_trace_id()
        await asyncio.sleep(0)
        if fail:
            raise RuntimeError("test failure")
        return {"before": before, "after": get_trace_id()}

    set_trace_id("parent")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            responses = await asyncio.gather(*[
                client.get("/trace/false", headers={"X-Trace-ID": f"request-{i}"})
                for i in range(5)
            ])
            for i, response in enumerate(responses):
                assert response.json() == {"before": f"request-{i}", "after": f"request-{i}"}
                assert response.headers["X-Trace-ID"] == f"request-{i}"
            with pytest.raises(RuntimeError, match="test failure"):
                await client.get("/trace/true", headers={"X-Trace-ID": "failed-request"})
        assert get_trace_id() == "parent"
    finally:
        set_trace_id("")
