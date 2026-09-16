"""Unit tests for the local ``NoopTracer`` (B5).

Offline/test double: ``install`` adds no middleware and ``current_trace_id``
returns None ⇒ no ``X-Trace-ID`` header (pre-seam local behavior).
"""
from __future__ import annotations

from fastapi import FastAPI

from agentclaw.community.plugins.local.tracer import NoopTracer


def test_current_trace_id_is_none():
    assert NoopTracer().current_trace_id() is None


def test_install_adds_no_middleware():
    app = FastAPI()
    before = len(app.user_middleware)
    NoopTracer().install(app)
    assert len(app.user_middleware) == before


def test_exports_no_trace_carrier():
    """Nothing is traced offline, so there is nothing for the task queue to
    persist — the columns stay NULL and the queue behaves as it did before
    trace carriage existed."""
    assert NoopTracer().export_trace_carrier() is None


def test_trace_scope_is_inert_and_never_raises():
    """Consumers enter this unconditionally (``TaskWorker`` does it for every
    task), so it has to be safe to call with anything, including a carrier
    written by some other impl."""
    tracer = NoopTracer()
    with tracer.trace_scope(None):
        assert tracer.current_trace_id() is None
    with tracer.trace_scope({"trace_id": "from-another-tracer"}):
        assert tracer.current_trace_id() is None
