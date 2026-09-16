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

import json

import pytest
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


# ── second consumer: carry a trace across a process boundary ────────────────
# ``TaskWorker`` exports the trace context at enqueue, persists it on the task
# row, and re-establishes it when the task is claimed — possibly minutes later,
# in another process, with nothing ambient. The Protocol contract that makes
# that work is the symmetry of ``export_trace_carrier`` and ``trace_scope``, so
# it is asserted here against every deployable impl rather than only where the
# queue happens to use it.


def _carried_trace(tracer: TracerPlugin) -> tuple[str | None, str | None]:
    """What the queue does, compressed: export under a request, restore after.

    Returns ``(id seen during the request, id seen during the restore)``. The
    carrier is round-tripped through JSON because that is what persisting it
    entails — a carrier that only survives in memory would pass a naive test and
    fail against the database.
    """
    app = FastAPI()
    captured: dict = {}

    @app.get("/enqueue")
    async def enqueue():
        captured["trace_id"] = tracer.current_trace_id()
        captured["carrier"] = tracer.export_trace_carrier()
        return {"ok": True}

    tracer.install(app)
    TestClient(app).get("/enqueue")

    stored = json.dumps(captured["carrier"]) if captured["carrier"] else None
    with tracer.trace_scope(json.loads(stored) if stored else None):
        restored = tracer.current_trace_id()
    return captured["trace_id"], restored


def test_community_tracer_carries_a_trace_across_the_boundary():
    enqueued, restored = _carried_trace(CommunityTracer())
    assert enqueued  # a request always has one under this impl
    assert restored == enqueued


def test_noop_tracer_carries_nothing_and_says_so():
    """No id to export, nothing to restore — and crucially no failure: a
    consumer must be able to run this path unconditionally."""
    assert _carried_trace(NoopTracer()) == (None, None)


def test_restoring_leaves_no_trace_behind():
    """The scope has to end with the block. In the worker one coroutine runs
    task after task, so a binding that outlived its scope would file every
    later task under the first one's trace."""
    tracer = CommunityTracer()
    with tracer.trace_scope({"trace_id": "carried"}):
        assert tracer.current_trace_id() == "carried"
    assert tracer.current_trace_id() is None


def test_restoring_survives_a_failing_body():
    """A task that raises must still unwind the trace — the worker's outcome
    handling runs after, and would otherwise log under a stale trace."""
    tracer = CommunityTracer()
    with pytest.raises(RuntimeError):
        with tracer.trace_scope({"trace_id": "carried"}):
            raise RuntimeError("handler blew up")
    assert tracer.current_trace_id() is None


def test_world_resolved_tracer_carries_nothing(world):
    """Rule 25 form for this consumer: the DI-bound tracer (test column →
    NoopTracer) exports nothing and restores nothing, without raising."""
    assert _carried_trace(world.get(TracerPlugin)) == (None, None)
