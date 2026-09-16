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


# ── carrying a trace beyond its request ─────────────────────────────────────


def test_exports_nothing_outside_a_request():
    assert CommunityTracer().export_trace_carrier() is None


def test_export_then_restore_round_trips_the_id():
    """The pair the task queue relies on. This impl has no call tree and no
    baggage, so its carrier holds only the id — but it travels the same path as
    the corp impl's, whose carrier holds propagation headers."""
    tracer = CommunityTracer()
    with tracer.trace_scope({"trace_id": "carried"}):
        carrier = tracer.export_trace_carrier()
    assert carrier

    with tracer.trace_scope(carrier):
        assert tracer.current_trace_id() == "carried"


def test_restoring_nothing_is_a_no_op():
    """A task enqueued outside any request has no carrier. Entering the scope
    anyway must be safe, so no caller has to check first."""
    tracer = CommunityTracer()
    with tracer.trace_scope(None):
        assert tracer.current_trace_id() is None
    with tracer.trace_scope({}):
        assert tracer.current_trace_id() is None


def test_restore_nests_and_unwinds():
    """Restores stack rather than overwrite: a handler that enqueues follow-up
    work runs inside its own restored scope, and what it sees afterwards must be
    what it saw before."""
    tracer = CommunityTracer()
    with tracer.trace_scope({"trace_id": "outer"}):
        with tracer.trace_scope({"trace_id": "inner"}):
            assert tracer.current_trace_id() == "inner"
        assert tracer.current_trace_id() == "outer"
    assert tracer.current_trace_id() is None
