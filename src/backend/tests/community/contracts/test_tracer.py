"""Rule 25 conformance — TracerPlugin.

Consumer under test: the trace-header behavior that ``TraceIdMappingMiddleware``
performs — read ``tracer.current_trace_id()`` and, when present, emit it as the
``X-Trace-ID`` response header. We exercise that consumer against the deployable
impls and assert the Protocol contract holds end-to-end:

- local ``NoopTracer`` → no id → no header (local/test parity).
- community ``CommunityTracer`` → a fresh per-request id → header present, distinct
  per request.

(The DI-resolved ``world`` form lands once the tracer column is wired — B5 Group B.
This self-contained consumer already proves consumer ↔ Protocol conformance.)
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from agentclaw.community.plugin_api.tracer import TracerPlugin
from agentclaw.community.plugins.community.tracer import CommunityTracer
from agentclaw.community.plugins.local.tracer import NoopTracer


def _consumer_app(tracer: TracerPlugin) -> FastAPI:
    """A miniature of TraceIdMappingMiddleware's consumer behavior.

    The consumer middleware is registered *before* ``tracer.install`` so the
    tracer's own middleware is outermost — it establishes the trace context
    before the consumer reads it (the real install ordering).
    """
    app = FastAPI()

    @app.middleware("http")
    async def _emit_trace_header(request, call_next):
        trace_id = tracer.current_trace_id()
        response = await call_next(request)
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id
        return response

    tracer.install(app)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return app


def test_noop_tracer_consumer_emits_no_header():
    client = TestClient(_consumer_app(NoopTracer()))
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert "x-trace-id" not in {k.lower() for k in resp.headers.keys()}


def test_community_tracer_consumer_emits_per_request_header():
    client = TestClient(_consumer_app(CommunityTracer()))
    r1 = client.get("/ping")
    r2 = client.get("/ping")
    assert r1.headers.get("X-Trace-ID")
    assert r2.headers.get("X-Trace-ID")
    assert r1.headers["X-Trace-ID"] != r2.headers["X-Trace-ID"]


def test_world_resolved_tracer_flows_through_consumer(world):
    """Canonical Rule 25 form: the DI-bound tracer (test column → NoopTracer)
    is resolved via the injector and flows through the consumer — no id ⇒ no
    ``X-Trace-ID`` header (local/test parity)."""
    tracer = world.get(TracerPlugin)
    client = TestClient(_consumer_app(tracer))
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert "x-trace-id" not in {k.lower() for k in resp.headers.keys()}
