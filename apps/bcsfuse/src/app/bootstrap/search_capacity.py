"""Bounded admission and Prometheus instrumentation for search traffic."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.application.services.query_intent_policy import resolve_query_intent
from app.application.services.query_normalizer import normalize_query_text
from app.infra.public.observability.search_stage_metrics import (
    search_stage_metrics,
)

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
        direct_keyword_max_inflight: int = 128,
        direct_keyword_max_queue: int = 128,
        direct_keyword_queue_wait_seconds: float = 2.0,
        local_fallback_max_inflight: int = 512,
        local_fallback_max_queue: int = 512,
        local_fallback_queue_wait_seconds: float = 0.25,
    ):
        if max_inflight <= 0:
            raise ValueError("search capacity max_inflight must be positive")
        if retry_after_seconds <= 0:
            raise ValueError("search capacity retry_after_seconds must be positive")
        if max_queue < 0:
            raise ValueError("search capacity max_queue must not be negative")
        if queue_wait_seconds <= 0:
            raise ValueError("search capacity queue_wait_seconds must be positive")
        if direct_keyword_max_inflight <= 0:
            raise ValueError(
                "direct keyword capacity max_inflight must be positive"
            )
        if direct_keyword_max_queue < 0:
            raise ValueError(
                "direct keyword capacity max_queue must not be negative"
            )
        if direct_keyword_queue_wait_seconds <= 0:
            raise ValueError(
                "direct keyword capacity queue_wait_seconds must be positive"
            )
        if local_fallback_max_inflight <= 0:
            raise ValueError(
                "local fallback capacity max_inflight must be positive"
            )
        if local_fallback_max_queue < 0:
            raise ValueError(
                "local fallback capacity max_queue must not be negative"
            )
        if local_fallback_queue_wait_seconds <= 0:
            raise ValueError(
                "local fallback capacity queue_wait_seconds must be positive"
            )
        self.max_inflight = max_inflight
        self.retry_after_seconds = retry_after_seconds
        self.max_queue = max_queue
        self.queue_wait_seconds = queue_wait_seconds
        self.direct_keyword_max_inflight = direct_keyword_max_inflight
        self.direct_keyword_max_queue = direct_keyword_max_queue
        self.direct_keyword_queue_wait_seconds = direct_keyword_queue_wait_seconds
        self.local_fallback_max_inflight = local_fallback_max_inflight
        self.local_fallback_max_queue = local_fallback_max_queue
        self.local_fallback_queue_wait_seconds = local_fallback_queue_wait_seconds

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
        direct_keyword_max_inflight = int(
            _config_get(
                context,
                "search.capacity.direct_keyword_max_inflight",
                128,
                "get_int",
            )
        )
        direct_keyword_max_queue = int(
            _config_get(
                context,
                "search.capacity.direct_keyword_max_queue",
                128,
                "get_int",
            )
        )
        direct_keyword_queue_wait_seconds = float(
            _config_get(
                context,
                "search.capacity.direct_keyword_queue_wait_seconds",
                2.0,
                "get_float",
            )
        )
        local_fallback_max_inflight = int(
            _config_get(
                context,
                "search.capacity.local_fallback_max_inflight",
                512,
                "get_int",
            )
        )
        local_fallback_max_queue = int(
            _config_get(
                context,
                "search.capacity.local_fallback_max_queue",
                512,
                "get_int",
            )
        )
        local_fallback_queue_wait_seconds = float(
            _config_get(
                context,
                "search.capacity.local_fallback_queue_wait_seconds",
                0.25,
                "get_float",
            )
        )
        return cls(
            max_inflight=max_inflight,
            retry_after_seconds=retry_after_seconds,
            max_queue=max_queue,
            queue_wait_seconds=queue_wait_seconds,
            direct_keyword_max_inflight=direct_keyword_max_inflight,
            direct_keyword_max_queue=direct_keyword_max_queue,
            direct_keyword_queue_wait_seconds=direct_keyword_queue_wait_seconds,
            local_fallback_max_inflight=local_fallback_max_inflight,
            local_fallback_max_queue=local_fallback_max_queue,
            local_fallback_queue_wait_seconds=local_fallback_queue_wait_seconds,
        )



class _AdmissionPool:
    """Bounded inflight and waiting state for one execution route."""

    def __init__(
        self,
        *,
        max_inflight: int,
        max_queue: int,
        queue_wait_seconds: float,
    ) -> None:
        self.max_inflight = max_inflight
        self.max_queue = max_queue
        self.queue_wait_seconds = queue_wait_seconds
        self.permits = asyncio.Semaphore(max_inflight)
        self.inflight = 0
        self.waiting = 0
        self.state_lock = threading.Lock()


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
        "route_rejections": Counter(
            "bcsfuse_search_capacity_route_rejections_total",
            "Capacity rejections split by execution route",
            [*operation_labels, "route"],
        ),
        "outage_admissions": Counter(
            "bcsfuse_search_outage_admissions_total",
            "Requests admitted to the process-local terminal fallback pool",
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
    rerank work. Semantic search/recommendation and zero-embedding direct
    lookups use separate bounded pools so one expensive route cannot starve the
    other while the process still has finite total work capacity.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: SearchCapacitySettings,
        metrics: dict[str, Any] | None = None,
        retrieval_dependency_circuit: Any | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self.retrieval_dependency_circuit = retrieval_dependency_circuit
        self._metrics = metrics or _get_search_metrics()
        self._pools = {
            "semantic": _AdmissionPool(
                max_inflight=settings.max_inflight,
                max_queue=settings.max_queue,
                queue_wait_seconds=settings.queue_wait_seconds,
            ),
            "direct_keyword": _AdmissionPool(
                max_inflight=settings.direct_keyword_max_inflight,
                max_queue=settings.direct_keyword_max_queue,
                queue_wait_seconds=settings.direct_keyword_queue_wait_seconds,
            ),
            "local_fallback": _AdmissionPool(
                max_inflight=settings.local_fallback_max_inflight,
                max_queue=settings.local_fallback_max_queue,
                queue_wait_seconds=settings.local_fallback_queue_wait_seconds,
            ),
        }
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
        state = scope.get("state")
        preflight_route = (
            state.get("bcsfuse_search_route")
            if isinstance(state, dict)
            else None
        )
        request_route = (
            preflight_route
            if preflight_route in {"semantic", "direct_keyword"}
            else _request_route(messages)
        )

        execution_route = self._execution_route(request_route)
        pool = self._pools[execution_route]
        if execution_route == "local_fallback":
            self._metrics["outage_admissions"].labels(**labels).inc()
        with search_stage_metrics(operation, "queue"):
            admitted = await self._admit(pool, operation, api_version, labels)
        if not admitted:
            response = self._reject(
                operation, api_version, labels,
                started_at=started_at,
                receive=receive, send=send,
                execution_route=execution_route,
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
            self._release(pool)

    def _execution_route(self, request_route: str) -> str:
        """Send terminal fallback traffic to its own bounded execution budget."""

        if (
            request_route == "semantic"
            and self.retrieval_dependency_circuit is not None
            and self.retrieval_dependency_circuit.outaged
        ):
            return "local_fallback"
        return request_route

    async def _admit(
        self,
        pool: _AdmissionPool,
        operation: str,
        api_version: str,
        labels: dict[str, str],
    ) -> bool:
        """Reserve one slot from the route's bounded execution-and-wait budget."""

        limits = pool.max_inflight + pool.max_queue
        with pool.state_lock:
            if pool.inflight + pool.waiting >= limits:
                return False
            pool.waiting += 1
            queued = pool.inflight + pool.waiting > pool.max_inflight

        queued_since = time.perf_counter()
        try:
            if queued:
                self._metrics["queued"].labels(**labels).inc()
                try:
                    await asyncio.wait_for(
                        pool.permits.acquire(),
                        timeout=pool.queue_wait_seconds,
                    )
                except TimeoutError:
                    return False
            else:
                await pool.permits.acquire()
        finally:
            if queued:
                self._metrics["queue_wait"].labels(**labels).observe(
                    time.perf_counter() - queued_since
                )
            with pool.state_lock:
                pool.waiting -= 1
        with pool.state_lock:
            pool.inflight += 1
        return True

    def _release(self, pool: _AdmissionPool) -> None:
        with pool.state_lock:
            pool.inflight -= 1
        pool.permits.release()
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
        execution_route: str,
    ) -> JSONResponse:
        self._metrics["rejections"].labels(**labels).inc()
        route_rejections = self._metrics.get("route_rejections")
        if route_rejections is not None:
            route_rejections.labels(**labels, route=execution_route).inc()
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


def _request_route(messages: list[Message]) -> str:
    """Classify a parsed request body without logging query content."""

    body = b"".join(message.get("body", b"") for message in messages)
    try:
        payload = json.loads(body)
        raw_query = payload.get("query") if isinstance(payload, dict) else None
        raw_question = (
            payload.get("question") if isinstance(payload, dict) else None
        )
        raw = raw_query or raw_question
        if not isinstance(raw, str):
            return "semantic"
        normalized = normalize_query_text(raw)
        intent = resolve_query_intent(normalized)
        return intent.route
    except Exception:
        return "semantic"


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
