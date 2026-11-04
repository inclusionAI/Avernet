"""Process-wide structured HTTP admission for BCSFuse."""

from __future__ import annotations

import time
from typing import Any

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Scope, Send


class HTTPAdmissionSettings:
    """Deployment values for the global HTTP request budget."""

    def __init__(self, *, max_concurrency: int, retry_after_seconds: float) -> None:
        if max_concurrency <= 0:
            raise ValueError("HTTP admission max_concurrency must be positive")
        if retry_after_seconds <= 0:
            raise ValueError("HTTP admission retry_after_seconds must be positive")
        self.max_concurrency = max_concurrency
        self.retry_after_seconds = retry_after_seconds

    @classmethod
    def from_context(cls, context: Any) -> "HTTPAdmissionSettings":
        config = getattr(context, "config", None)
        if config is None:
            config = getattr(context, "registry", None)
            config = config.get("config") if config is not None else None
        get_int = getattr(config, "get_int", None)
        if get_int is not None:
            max_concurrency = get_int("http.capacity.max_concurrency", 512)
        else:
            max_concurrency = 512
        get_float = getattr(config, "get_float", None)
        if get_float is not None:
            retry_after_seconds = get_float(
                "http.capacity.retry_after_seconds", 1.0
            )
        else:
            retry_after_seconds = 1.0
        return cls(
            max_concurrency=int(max_concurrency),
            retry_after_seconds=float(retry_after_seconds),
        )


_HTTP_METRICS: dict[str, Any] | None = None


def _get_http_admission_metrics() -> dict[str, Any]:
    global _HTTP_METRICS
    if _HTTP_METRICS is not None:
        return _HTTP_METRICS

    from prometheus_client import Counter, Gauge, Histogram

    _HTTP_METRICS = {
        "rejected": Counter(
            "bcsfuse_http_admission_rejections_total",
            "HTTP requests rejected because the process request budget was occupied",
        ),
        "inflight": Gauge(
            "bcsfuse_http_admission_requests_inflight",
            "HTTP requests currently inside the process admission boundary",
        ),
        "duration": Histogram(
            "bcsfuse_http_admission_duration_seconds",
            "HTTP request duration inside the process admission boundary",
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
        ),
    }
    return _HTTP_METRICS


class HTTPAdmissionMiddleware:
    """Bound concurrent request tasks and return a stable 503 contract."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_concurrency: int,
        retry_after_seconds: float = 1.0,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self.app = app
        self.max_concurrency = max_concurrency
        self.retry_after_seconds = retry_after_seconds
        self._inflight = 0
        self._metrics = metrics or _get_http_admission_metrics()

    async def __call__(self, scope: Scope, receive: Any, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._inflight >= self.max_concurrency:
            response = self._reject()
            await response(scope, receive, send)
            return

        self._inflight += 1
        inflight = self._metrics["inflight"]
        inflight.inc()
        started_at = time.perf_counter()
        try:
            await self.app(scope, receive, send)
        finally:
            self._inflight -= 1
            inflight.dec()
            self._metrics["duration"].observe(time.perf_counter() - started_at)

    def _reject(self) -> JSONResponse:
        self._metrics["rejected"].inc()
        return JSONResponse(
            status_code=503,
            headers={
                "Retry-After": str(max(1, int(self.retry_after_seconds)))
            },
            content={
                "detail": {
                    "code": "HTTP_CAPACITY_EXCEEDED",
                    "message": "BCSFuse HTTP capacity is occupied",
                }
            },
        )


__all__ = [
    "HTTPAdmissionMiddleware",
    "HTTPAdmissionSettings",
]
