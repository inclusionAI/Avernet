"""TracerPlugin — request-tracing capability.

Tracing used to be hard-wired into the adapter layer: the HTTP middleware imported
the corp tracer SDK directly and read the trace id from the global tracing scope.
This Protocol moves every half of that — "instrument the app", "what is the
current trace id", and "carry a trace to work that outlives its request" —
behind a capability, so the neutral layers depend on the Protocol instead of on
any vendor SDK.

The last of those exists because a trace is task-local and a queued task is not:
``ac_task_queue`` work is submitted by one request and run minutes later by
another process, where nothing is ambient. ``export_trace_carrier`` and
``trace_scope`` are the two ends of that gap — the queue persists what the first
returns and hands it to the second, without ever looking inside it.

Each impl carries its own backend:
- ``plugins.prod.tracer`` — the corp tracer (tracing middleware + span).
- ``plugins.community.tracer.CommunityTracer`` — a self-minted per-request id, no exporter.
- ``plugins.local.tracer.NoopTracer`` — no tracing (no ``X-Trace-ID``).
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Optional, Protocol

from agentclaw.community.plugin_api.base import Plugin

if TYPE_CHECKING:
    from fastapi import FastAPI

#: A serialized trace context, as produced by :meth:`TracerPlugin.export_trace_carrier`
#: and consumed by :meth:`TracerPlugin.trace_scope`. Opaque to every caller: the
#: keys and their meaning belong to the tracer implementation (the corp impl's
#: are SofaTracer's propagation headers), so nothing outside a ``TracerPlugin``
#: may read, write, or reason about its contents. It is JSON-serializable, which
#: is the one property callers may rely on — that is what lets a trace context be
#: persisted across a process boundary.
TraceCarrier = dict


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

    def export_trace_carrier(self) -> Optional[TraceCarrier]:
        """Serialize the active trace context, or ``None`` when none is active.

        The counterpart of :meth:`trace_scope`: what this returns is exactly
        what that accepts. Work that outlives its request — a task persisted to
        ``ac_task_queue`` and run minutes later by another pod — carries this
        value along and hands it back on the way in, so the execution logs under
        the trace id of the request that asked for it.

        A **whole carrier** rather than the trace id alone, because a trace
        context is more than its id: the corp tracer's also carries the rpc id
        (position in the call tree) and sofa's penetration attributes. Exporting
        through the tracer keeps that format the tracer's business — no caller
        hardcodes a propagation header, and a change in the tracer's wire format
        does not reach into the queue's schema.

        Never raises: a tracer that cannot export returns ``None``, and the
        caller simply persists nothing.
        """
        ...

    def trace_scope(
        self, carrier: Optional[TraceCarrier]
    ) -> AbstractContextManager[None]:
        """Re-establish the trace context in ``carrier`` for the block's duration.

        Declared as returning a context manager rather than decorated with
        ``@contextmanager`` here: the decorator belongs to an implementation's
        chosen mechanism, and both shapes — a generator function and a
        hand-written ``__enter__``/``__exit__`` — satisfy this contract.

        Used by ``TaskWorker`` to run a queued task under the trace of the
        request that enqueued it. ``None`` — the tracer exported nothing, the row
        predates this feature, the impl does not trace — is a no-op, so a caller
        never has to check first.

        **Must not raise.** Tracing is diagnostic: a task that cannot be traced
        still has to run, so an impl that fails to restore logs and yields rather
        than propagating. It must also leave the ambient context exactly as it
        found it, including when the body raises.
        """
        ...
