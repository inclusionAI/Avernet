"""
TraceIdMiddleware — 统一管理请求级别的 trace_id

拦截所有请求，按优先级确定 trace_id：
1. X-Trace-ID header（上游服务透传）
2. X-Request-ID header（前端传入，与 backend 一致）
3. 自动生成（trace_{ts}_{random}）

生命周期：
- 请求进入 → 设置 trace_id 到 contextvars
- 请求结束 → 将 trace_id 注入响应 header X-Trace-ID
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.infra.trace_context import generate_trace_id, get_trace_id, set_trace_id


class TraceIdMiddleware(BaseHTTPMiddleware):
    """
    统一管理请求级别的 trace_id。

    优先级：
    1. X-Trace-ID header（上游服务透传）
    2. X-Request-ID header（前端传入，与 backend 一致）
    3. 自动生成（trace_{ts}_{random}）
    """

    async def dispatch(self, request: Request, call_next):
        # 确定 trace_id：上游传入优先，否则自动生成
        trace_id = (
            request.headers.get("X-Trace-ID")
            or request.headers.get("X-Request-ID")
            or generate_trace_id()
        )
        set_trace_id(trace_id)

        response = await call_next(request)

        # 响应 header 中也带上，方便网关/下游读取
        response.headers["X-Trace-ID"] = get_trace_id()
        return response


__all__ = ["TraceIdMiddleware"]