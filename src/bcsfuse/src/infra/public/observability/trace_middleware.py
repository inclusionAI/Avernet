"""Request tracing shared by composed and legacy apps without legacy imports."""

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.infra.trace_context import bind_trace_id, generate_trace_id


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        trace_id = headers.get("X-Trace-ID") or headers.get("X-Request-ID") or generate_trace_id()

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Trace-ID"] = trace_id
            await send(message)

        with bind_trace_id(trace_id):
            await self.app(scope, receive, send_with_trace)
