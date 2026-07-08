"""Unit tests for the community ``CommunityTracer`` (B5).

A real, deployable tracer with no exporter: it mints a fresh server-side trace
id per request (exposed via ``current_trace_id``) so ``X-Trace-ID`` keeps
populating under ``community``. The id is never an echo of an inbound
``X-Request-ID``.
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from agentclaw.community.plugins.community.tracer import CommunityTracer
from agentclaw.community.plugins.local._mock_seam import MockSeam


def _app_exposing_trace_id() -> tuple[FastAPI, CommunityTracer]:
    tracer = CommunityTracer()
    app = FastAPI()
    tracer.install(app)

    @app.get("/whoami")
    async def whoami():
        # Read inside the request: the contextvar is set by the tracer middleware.
        return {"trace_id": tracer.current_trace_id()}

    return app, tracer


def test_not_a_mock_seam():
    # Community impls must be real, not MockSeam doubles.
    assert not isinstance(CommunityTracer(), MockSeam)


def test_trace_id_is_none_outside_a_request():
    assert CommunityTracer().current_trace_id() is None


def test_trace_id_present_inside_a_request():
    app, _ = _app_exposing_trace_id()
    client = TestClient(app)
    body = client.get("/whoami").json()
    assert body["trace_id"]  # non-empty hex
    assert len(body["trace_id"]) == 32


def test_each_request_gets_a_distinct_id():
    app, _ = _app_exposing_trace_id()
    client = TestClient(app)
    id1 = client.get("/whoami").json()["trace_id"]
    id2 = client.get("/whoami").json()["trace_id"]
    assert id1 and id2 and id1 != id2


def test_trace_id_is_not_an_echo_of_request_id():
    app, _ = _app_exposing_trace_id()
    client = TestClient(app)
    body = client.get("/whoami", headers={"X-Request-ID": "client-supplied-123"}).json()
    assert body["trace_id"] != "client-supplied-123"


def test_dispatch_sets_id_during_and_resets_after():
    # Drive the tracer middleware directly in a single task context so set()
    # and reset() are both observable: the id is present while call_next runs
    # and gone afterward (finally-block reset — no leak across requests).
    import asyncio

    from agentclaw.community.plugins.community.tracer import (
        _RequestTraceContextMiddleware,
        _TRACE_ID,
    )

    async def _run() -> dict:
        seen: dict = {}

        async def call_next(_request):
            seen["during"] = _TRACE_ID.get()
            return "resp"

        mw = _RequestTraceContextMiddleware(app=None)
        await mw.dispatch(request=None, call_next=call_next)
        seen["after"] = _TRACE_ID.get()
        return seen

    seen = asyncio.run(_run())
    assert seen["during"] and len(seen["during"]) == 32
    assert seen["after"] is None
