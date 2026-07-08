"""Community ``TracerPlugin`` — a self-minted per-request id, no exporter.

A real, deployable tracer for the open-source build: it has no collector/backend,
but still mints a fresh server-side trace id for each request so the ``X-Trace-ID``
response header keeps populating under ``community``. The id lives in a
``contextvars.ContextVar`` set by a tiny middleware this tracer installs.

Depends on no corp tracer SDK — pure in-process id minting. Not a ``MockSeam``
subclass: this is a real impl bound directly by ``CommunityTracerModule``.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware

from agentclaw.community.plugin_api.tracer import TracerPlugin

if TYPE_CHECKING:
    from fastapi import FastAPI

# Task-local current trace id. Default None ⇒ no id outside a request.
_TRACE_ID: ContextVar[str | None] = ContextVar("community_trace_id", default=None)


class _RequestTraceContextMiddleware(BaseHTTPMiddleware):
    """Mint a fresh trace id per request and expose it via ``_TRACE_ID``.

    The id is server-minted (``uuid4().hex``) — deliberately NOT an echo of any
    inbound ``X-Request-ID`` header, matching the prod invariant that the
    server's trace id is its own, never the client's request id.
    """

    async def dispatch(self, request, call_next):
        token = _TRACE_ID.set(uuid4().hex)
        try:
            return await call_next(request)
        finally:
            _TRACE_ID.reset(token)


class CommunityTracer(TracerPlugin):
    """Real community tracer: per-request id, no exporter."""

    def install(self, app: "FastAPI") -> None:
        app.add_middleware(_RequestTraceContextMiddleware)

    def current_trace_id(self) -> str | None:
        return _TRACE_ID.get()
