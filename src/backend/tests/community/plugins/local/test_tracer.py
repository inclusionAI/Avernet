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
