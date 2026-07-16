"""Tracer plugin Protocol — distributed tracing abstraction.

Implementations provide ``setup``, ``install_middleware``,
``get_trace_id``, and trace-context propagation methods so that the
application can initialise tracing and propagate context across async
boundaries independently of the backend (SOFA RPC native tracing or
OpenTelemetry).
"""

from __future__ import annotations

from typing import Any, Protocol


class TracerPlugin(Protocol):
    """Plugin protocol for distributed tracing.

    Implementations:
    - SofaTracerPlugin: uses SOFA RPC native tracing (patches + middleware).
    - BareTracerPlugin: uses OpenTelemetry with OTLP/Console exporters.
    """

    def setup(self, app_name: str) -> None:
        """Initialise the tracing system.

        Args:
            app_name: Application name used as the service name.
        """
        ...

    def install_middleware(self, app: Any) -> None:
        """Install tracing middleware on a FastAPI application.

        Args:
            app: The FastAPI application instance (typed as ``Any`` to
                avoid a hard dependency on FastAPI at the SPI level).
        """
        ...

    def get_trace_id(self) -> str:
        """Return the current trace ID as a hex string.

        Returns:
            A 32-character hex trace ID string, or ``"-"`` if no
            active span exists.
        """
        ...

    def capture_context(self) -> Any:
        """Capture the current trace context for later restoration.

        Called on the request side to snapshot the active trace context
        so it can be restored in a different async task (e.g. WebSocket
        callbacks).  Returns an opaque object whose type is specific to
        the tracing backend, or ``None`` when no valid span is active.
        """
        ...

    def attach_context(self, ctx: Any) -> Any:
        """Restore a previously captured trace context.

        Activates the given context as the current trace context.
        Returns an opaque *token* that must be passed to
        :meth:`detach_context` to restore the previous context.
        """
        ...

    def detach_context(self, token: Any) -> None:
        """Restore the previous trace context.

        Args:
            token: The value returned by :meth:`attach_context`.
        """
        ...
