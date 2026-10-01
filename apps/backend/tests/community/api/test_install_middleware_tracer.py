"""``install_middleware`` wires the tracer outermost — X-Trace-ID flows.

Guards the ordering invariant **through the real ``install_middleware``**
(not a hand-rolled consumer): the tracer's own middleware must be installed
*outermost* so ``TraceIdMappingMiddleware`` sees the trace context when it
reads ``current_trace_id()``. A reversed order would silently drop the header
— which the fixed-id ``_FakeTracer`` unit tests cannot catch, but a real
context-establishing tracer (``CommunityTracer``) does.
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from agentclaw.community.adapters.http.middleware import install_middleware
from agentclaw.community.plugins.community.tracer import CommunityTracer
from agentclaw.community.plugins.local.tracer import NoopTracer


class _FakeAuth:
    """Minimal AuthPlugin stand-in — UserContextMiddleware tolerates any
    auth outcome (user stays None on failure)."""

    async def resolve_user_from_request(self, ctx):
        return None


def _client(tracer) -> TestClient:
    app = FastAPI()
    install_middleware(app, auth_plugin=_FakeAuth(), tracer=tracer)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    return TestClient(app)


def test_community_tracer_flows_through_real_install_middleware():
    # The community tracer's context middleware (installed outermost by
    # install_middleware) sets a per-request id that TraceIdMappingMiddleware
    # reads — proving the install order is correct, not reversed.
    client = _client(CommunityTracer())
    r1 = client.get("/ok")
    r2 = client.get("/ok")
    assert r1.headers.get("X-Trace-ID")
    assert r2.headers.get("X-Trace-ID")
    assert r1.headers["X-Trace-ID"] != r2.headers["X-Trace-ID"]


def test_noop_tracer_emits_no_header_through_real_install_middleware():
    client = _client(NoopTracer())
    resp = client.get("/ok")
    assert "x-trace-id" not in {k.lower() for k in resp.headers.keys()}
