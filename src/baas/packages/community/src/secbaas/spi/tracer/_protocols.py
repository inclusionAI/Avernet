"""Tracer plugin Protocol — distributed tracing abstraction.

Implementations provide ``setup``, ``install_middleware``, and
``get_trace_id`` so that the application can initialise tracing
independently of the backend (SOFA RPC native tracing or
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
