"""Trace context must survive composition and remain request-local."""

import pytest
import asyncio
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bootstrap.opensource_app import create_opensource_app
from src.infra.trace_context import get_trace_id, set_trace_id
from src.infra.public.observability.trace_middleware import TraceIdMiddleware


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
