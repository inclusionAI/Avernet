"""TracerPlugin — request-tracing capability.

Tracing used to be hard-wired into the adapter layer: the HTTP middleware imported
the corp tracer SDK directly and read the trace id from the global tracing scope.
This Protocol moves both halves — "instrument the app" and "what is the current
trace id" — behind a capability, so the neutral layers depend on the Protocol
instead of on any vendor SDK.

Each impl carries its own backend:
- ``plugins.prod.tracer`` — the corp tracer (tracing middleware + span).
- ``plugins.community.tracer.CommunityTracer`` — a self-minted per-request id, no exporter.
- ``plugins.local.tracer.NoopTracer`` — no tracing (no ``X-Trace-ID``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from agentclaw.community.plugin_api.base import Plugin

if TYPE_CHECKING:
    from fastapi import FastAPI


class TracerPlugin(Plugin, Protocol):
    """Per-runtime request tracing."""

    def install(self, app: "FastAPI") -> None:
        """Instrument ``app`` (add the tracing middleware / patches).

        Called from ``install_middleware`` *after* ``TraceIdMappingMiddleware``
        so the tracer's middleware is outermost — it establishes the trace
        context before ``TraceIdMappingMiddleware`` reads it. May be a no-op.
        """
        ...

    def current_trace_id(self) -> str | None:
        """The active request's trace id (task-local), or ``None`` when no
        tracer is active. Read by ``TraceIdMappingMiddleware`` to populate the
        ``X-Trace-ID`` response header."""
        ...
