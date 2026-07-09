"""Tracer concern — test / singlebox binding (NoopTracer)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.tracer import TracerPlugin


class TestTracerModule(Module):
    """test / singlebox: NoopTracer (no tracing, no X-Trace-ID)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.tracer import NoopTracer

        binder.bind(TracerPlugin, to=NoopTracer, scope=singleton)
