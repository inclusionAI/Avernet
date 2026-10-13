"""Request tracing shared by composed and legacy apps without legacy imports."""

import logging
from time import perf_counter

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.infra.trace_context import (
    bind_trace_id,
    generate_trace_id,
    install_trace_record_factory,
)

logger = logging.getLogger(__name__)


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Composed/internal runners do not necessarily call configure_logging().
        # Preserve their handlers and formatters; only enrich records.
        install_trace_record_factory()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        trace_id = headers.get("X-Trace-ID") or headers.get("X-Request-ID") or generate_trace_id()
        path = scope.get("path", "")
        is_online = scope.get("method") == "PUT" and path.endswith("/online") and "/workers/" in path
        started_at = perf_counter() if is_online else None
        status_code = None
        if is_online:
            logger.info("[ONLINE-TIMING] phase=request_start path=%s trace_id=%s", path, trace_id)

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                nonlocal status_code
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Trace-ID"] = trace_id
            await send(message)

        with bind_trace_id(trace_id):
            try:
                await self.app(scope, receive, send_with_trace)
            finally:
                if started_at is not None:
                    logger.info(
                        "[ONLINE-TIMING] phase=request_end path=%s trace_id=%s status=%s elapsed_ms=%.1f",
                        path, trace_id, status_code, (perf_counter() - started_at) * 1000,
                    )
