"""Bounded admission and Prometheus instrumentation for search traffic."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_SEARCH_BODY_BYTES = 64 * 1024

_SEARCH_PATHS = {
    "/api/v1/search": ("search", "v1"),
    "/api/v1/recommend": ("recommend", "v1"),
    "/api/v2/search": ("search", "v2"),
    "/api/v2/recommend": ("recommend", "v2"),
}


class SearchCapacitySettings:
    """Read deployment capacity through the composed config provider."""

    def __init__(
        self,
        *,
        max_inflight: int,
        retry_after_seconds: float,
        max_queue: int = 128,
        queue_wait_seconds: float = 2.0,
    ):
        if max_inflight <= 0:
            raise ValueError("search capacity max_inflight must be positive")
        if retry_after_seconds <= 0:
            raise ValueError("search capacity retry_after_seconds must be positive")
        if max_queue < 0:
            raise ValueError("search capacity max_queue must not be negative")
        if queue_wait_seconds <= 0:
            raise ValueError("search capacity queue_wait_seconds must be positive")
        self.max_inflight = max_inflight
        self.retry_after_seconds = retry_after_seconds
        self.max_queue = max_queue
        self.queue_wait_seconds = queue_wait_seconds

    @classmethod
    def from_context(cls, context: Any) -> "SearchCapacitySettings":
        max_inflight = int(
            _config_get(context, "search.capacity.max_inflight", 128, "get_int")
        )
        retry_after_seconds = float(
            _config_get(
                context,
                "search.capacity.retry_after_seconds",
                1.0,
                "get_float",
            )
        )
        max_queue = int(
            _config_get(context, "search.capacity.max_queue", 128, "get_int")
        )
        queue_wait_seconds = float(
            _config_get(
                context,
                "search.capacity.queue_wait_seconds",
                2.0,
                "get_float",
            )
        )
        return cls(
            max_inflight=max_inflight,
            retry_after_seconds=retry_after_seconds,
            max_queue=max_queue,
            queue_wait_seconds=queue_wait_seconds,
        )


_SEARCH_METRICS: dict[str, Any] | None = None


def _get_search_metrics() -> dict[str, Any]:
    global _SEARCH_METRICS
    if _SEARCH_METRICS is not None:
        return _SEARCH_METRICS

    from prometheus_client import Counter, Gauge, Histogram

    operation_labels = ["operation", "api_version"]
    request_labels = ["operation", "api_version", "outcome"]
    _SEARCH_METRICS = {
        "requests": Counter(
            "bcsfuse_search_requests_total",
            "Search and recommendation requests completed",
            request_labels,
        ),
        "rejections": Counter(
            "bcsfuse_search_capacity_rejections_total",
            "Requests rejected before admission because search capacity was occupied",
            operation_labels,
        ),
        "inflight": Gauge(
            "bcsfuse_search_requests_inflight",
            "Currently admitted search or recommendation requests",
            operation_labels,
        ),
        "capacity": Gauge(
            "bcsfuse_search_capacity_max_inflight",
            "Configured maximum simultaneous search requests",
        ),
        "queued": Counter(
            "bcsfuse_search_capacity_queued_total",
            "Search requests that waited for a free execution slot",
            operation_labels,
        ),
        "queue_wait": Histogram(
            "bcsfuse_search_capacity_queue_wait_seconds",
            "Admission queue wait before business execution starts",
            operation_labels,
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 2),
        ),
        "queue_capacity": Gauge(
            "bcsfuse_search_capacity_max_queue",
            "Configured maximum waiting search requests",
        ),
        "too_large_requests": Counter(
            "bcsfuse_search_request_too_large_total",
            "Search requests rejected before admission because the body was too large",
            operation_labels,
        ),
        "body_read_errors": Counter(
            "bcsfuse_search_body_read_errors_total",
            "Search request body transport failures before admission",
            operation_labels,
        ),
        "duration": Histogram(
            "bcsfuse_search_duration_seconds",
            "Search and recommendation HTTP request duration",
            request_labels,
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
        ),
    }
    return _SEARCH_METRICS


class SearchCapacityMiddleware:
    """Apply one bounded process-wide budget to search and recommendation.

    Capacity admission happens before business service execution. A rejected call
    receives a retry hint and never creates database, embedding, Qdrant, or
    rerank work. The budget is intentionally shared across v1/v2 and
    search/recommend because one event loop has one finite admission pool.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: SearchCapacitySettings,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self._metrics = metrics or _get_search_metrics()
        self._permits = asyncio.Semaphore(settings.max_inflight)
        self._inflight_count = 0
        self._waiting_count = 0
        self._state_lock = threading.Lock()
        self._metrics["capacity"].set(settings.max_inflight)
        self._metrics["queue_capacity"].set(settings.max_queue)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        target = _SEARCH_PATHS.get(scope.get("path", ""))
        if target is None or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        operation, api_version = target
        labels = {"operation": operation, "api_version": api_version}
        started_at = time.perf_counter()

        declared_size = _declared_content_length(scope)
        if declared_size is not None and declared_size > MAX_SEARCH_BODY_BYTES:
            response = self._reject_too_large(
                operation, api_version, started_at=started_at
            )
            await response(scope, receive, send)
            return

        # Payment for business work starts only after the request body has been
        # fully loaded under the transport boundary. A slow or failed upload
        # must not consume a Qdrant/model admission slot.
        app_receive: Receive = receive
        if declared_size is None:
            try:
                messages, body_too_large = await self._read_bounded_request(receive)
            except Exception:
                self._metrics["body_read_errors"].labels(**labels).inc()
                raise
            if body_too_large:
                response = self._reject_too_large(
                    operation, api_version, started_at=started_at
                )
                await response(scope, receive, send)
                return
            app_receive = _replayed_receive(messages)

        if not await self._admit(operation, api_version, labels):
            response = self._reject(
                operation, api_version, labels,
                started_at=started_at,
                receive=receive, send=send,
            )
            await response(scope, receive, send)
            return

        inflight = self._metrics["inflight"].labels(**labels)
        inflight.inc()
        status_code: int | None = None
        try:
            async def send_with_status(message: Message) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = int(message["status"])
                await send(message)

            await self.app(scope, app_receive, send_with_status)
        finally:
            outcome = str(status_code or 500)
            self._record(operation, api_version, outcome, time.perf_counter() - started_at)
            inflight.dec()
            with self._state_lock:
                self._inflight_count -= 1
            self._permits.release()

    async def _admit(
        self,
        operation: str,
        api_version: str,
        labels: dict[str, str],
    ) -> bool:
        """Reserve one slot from the bounded execution-and-wait budget."""

        limits = self.settings.max_inflight + self.settings.max_queue
        with self._state_lock:
            if (
                self._inflight_count + self._waiting_count
                >= limits
            ):
                return False
            self._waiting_count += 1
            queued = (
                self._inflight_count + self._waiting_count
                > self.settings.max_inflight
            )

        queued_since = time.perf_counter()
        try:
            if queued:
                self._metrics["queued"].labels(**labels).inc()
                try:
                    await asyncio.wait_for(
                        self._permits.acquire(),
                        timeout=self.settings.queue_wait_seconds,
                    )
                except TimeoutError:
                    return False
            else:
                await self._permits.acquire()
        finally:
            if queued:
                self._metrics["queue_wait"].labels(**labels).observe(
                    time.perf_counter() - queued_since
                )
            with self._state_lock:
                self._waiting_count -= 1
        with self._state_lock:
            self._inflight_count += 1
        return True
    async def _read_bounded_request(self, receive: Receive) -> tuple[list[Message], bool]:
        """Consume an undeclared body while bounding total search request bytes."""
        messages = []
        total_bytes = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                if message["type"] == "http.disconnect":
                    return messages, False
                continue
            messages.append(message)
            total_bytes += len(message.get("body", b""))
            if total_bytes > MAX_SEARCH_BODY_BYTES:
                return messages, True
            if not message.get("more_body", False):
                return messages, False

    def _reject_too_large(
        self, operation: str, api_version: str, *, started_at: float
    ) -> JSONResponse:
        self._metrics["too_large_requests"].labels(
            operation=operation, api_version=api_version
        ).inc()
        self._record(
            operation, api_version, "413", time.perf_counter() - started_at
        )
        return JSONResponse(
            status_code=413,
            content={
                "detail": {
                    "code": "SEARCH_REQUEST_TOO_LARGE",
                    "message": "Search request body exceeds 64 KiB",
                }
            },
        )

    def _reject(
        self,
        operation: str,
        api_version: str,
        labels: dict[str, str],
        *,
        started_at: float,
        receive: Receive,
        send: Send,
    ) -> JSONResponse:
        self._metrics["rejections"].labels(**labels).inc()
        self._record(
            operation, api_version, "429", time.perf_counter() - started_at
        )
        response = JSONResponse(
            status_code=429,
            headers={
                "Retry-After": str(max(1, int(self.settings.retry_after_seconds)))
            },
            content={
                "detail": {
                    "code": "SEARCH_CAPACITY_EXCEEDED",
                    "message": "Search capacity is occupied",
                }
            },
        )
        return response

    def _record(
        self, operation: str, api_version: str, outcome: str, duration: float
    ) -> None:
        labels = {"operation": operation, "api_version": api_version}
        self._metrics["requests"].labels(**labels, outcome=outcome).inc()
        self._metrics["duration"].labels(**labels, outcome=outcome).observe(duration)


def _declared_content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if bytes(name).lower() != b"content-length":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _replayed_receive(messages: list[Message]) -> Receive:
    iterator = iter(messages)

    async def receive() -> Message:
        try:
            return next(iterator)
        except StopIteration:
            return {"type": "http.disconnect"}

    return receive


def _config_get(
    context: Any, key: str, default: Any, method_name: str
):
    config = context.config
    if config is None:
        return default
    method = getattr(config, method_name, None)
    if method is None:
        return default
    return method(key, default)


__all__ = [
    "SearchCapacityMiddleware",
    "SearchCapacitySettings",
]
