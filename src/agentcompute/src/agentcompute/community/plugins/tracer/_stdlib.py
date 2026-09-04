"""Contextvar-based TracerPlugin implementation (stdlib ``uuid`` trace ids)."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from ...spi._tracer import TracerPlugin

__all__ = ["StdlibTracerPlugin"]

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


class StdlibTracerPlugin(TracerPlugin):
    """Tracer that assigns a per-request trace id with a ``ContextVar``."""

    def __init__(self) -> None:
        self._app_name = "agentcompute"

    def setup(self, app_name: str) -> None:
        self._app_name = app_name

    def install_middleware(self, app: Any) -> None:
        from starlette.requests import Request
        from starlette.responses import Response

        @app.middleware("http")  # type: ignore[untyped-decorator]
        async def trace_id_middleware(request: Request, call_next: Any) -> Response:
            trace_id = str(uuid.uuid4())
            token = _trace_id.set(trace_id)
            try:
                response: Response = await call_next(request)
            finally:
                _trace_id.reset(token)
            response.headers["X-Trace-Id"] = trace_id
            return response

    def get_trace_id(self) -> str:
        return _trace_id.get()
