"""Community ``TracerPlugin`` — a self-minted per-request id, no exporter.

A real, deployable tracer for the open-source build: it has no collector/backend,
but still mints a fresh server-side trace id for each request so the ``X-Trace-ID``
response header keeps populating under ``community``. The id lives in a
``contextvars.ContextVar`` set by a tiny middleware this tracer installs.

Depends on no corp tracer SDK — pure in-process id minting. Not a ``MockSeam``
subclass: this is a real impl bound directly by ``CommunityTracerModule``.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Iterator, Optional
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware

from agentclaw.community.plugin_api.tracer import TraceCarrier, TracerPlugin

if TYPE_CHECKING:
    from fastapi import FastAPI

# Task-local current trace id. Default None ⇒ no id outside a request.
_TRACE_ID: ContextVar[str | None] = ContextVar("community_trace_id", default=None)

#: The carrier key this tracer serializes its context under. A private detail of
#: this impl — the corp tracer uses SofaTracer's propagation headers instead, and
#: nothing outside a ``TracerPlugin`` may read either spelling.
_CARRIER_TRACE_ID = "trace_id"


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

    def export_trace_carrier(self) -> Optional[TraceCarrier]:
        """The current id in a one-key carrier, or ``None`` outside a request.

        There is nothing else to carry: this tracer has no call tree and no
        baggage, so the carrier the corp impl fills with propagation headers
        holds a single id here. The shape still goes through the carrier rather
        than being special-cased by callers, so both impls travel the same path
        and the queue's stored value means the same thing under either.
        """
        trace_id = _TRACE_ID.get()
        if not trace_id:
            return None
        return {_CARRIER_TRACE_ID: trace_id}

    @contextmanager
    def trace_scope(self, carrier: Optional[TraceCarrier]) -> Iterator[None]:
        """Bind the carrier's id for the block, restoring the previous one after.

        The ``reset`` in ``finally`` runs even when the body raises, so a
        restored trace never outlives its scope — which matters in the worker,
        where one coroutine runs many tasks in turn.
        """
        trace_id = carrier.get(_CARRIER_TRACE_ID) if carrier else None
        if not trace_id:
            yield
            return
        token = _TRACE_ID.set(str(trace_id))
        try:
            yield
        finally:
            _TRACE_ID.reset(token)
