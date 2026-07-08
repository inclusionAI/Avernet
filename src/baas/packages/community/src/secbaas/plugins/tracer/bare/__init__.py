"""OpenTelemetry tracer plugin — OTLP/Console exporters.

Extracted from ``adapters/web/app.py``: reads ``OTEL_TRACES_EXPORTER``
env var and configures ``TracerProvider`` with OTLP, Console, or none
exporter.
"""

from __future__ import annotations

__all__ = [
    "BareTracerPlugin",
]

import os
from typing import Any

from secbaas.spi.tracer import TracerPlugin


class BareTracerPlugin(TracerPlugin):
    """Tracer plugin that uses OpenTelemetry with configurable exporters.

    Exporters are selected via the ``OTEL_TRACES_EXPORTER`` env var
    (comma-separated: ``otlp``, ``console``, ``none``).
    """

    def setup(self, app_name: str) -> None:
        """Initialise OpenTelemetry tracing."""
        exporter_kind = os.getenv("OTEL_TRACES_EXPORTER", "none")

        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        service_name = os.getenv("OTEL_SERVICE_NAME", app_name)
        resource = Resource(attributes={"service.name": service_name})
        provider = TracerProvider(resource=resource)

        if exporter_kind != "none":
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            for kind in exporter_kind.split(","):
                kind = kind.strip()
                if kind == "otlp":
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                        OTLPSpanExporter,
                    )

                    otlp_endpoint = os.getenv(
                        "OTEL_EXPORTER_OTLP_ENDPOINT",
                        "http://localhost:4318",
                    )
                    provider.add_span_processor(
                        BatchSpanProcessor(
                            OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"),
                        ),
                    )
                elif kind == "console":
                    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

                    provider.add_span_processor(
                        BatchSpanProcessor(ConsoleSpanExporter()),
                    )

        trace.set_tracer_provider(provider)

    def install_middleware(self, app: Any) -> None:
        """Install the OpenTelemetry ASGI middleware."""
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

        app.add_middleware(OpenTelemetryMiddleware)

    def get_trace_id(self) -> str:
        """Return the current trace ID as a 32-character hex string."""
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is not None:
            ctx = span.get_span_context()
            if ctx is not None and ctx.is_valid:
                return format(ctx.trace_id, "032x")
        return "-"
