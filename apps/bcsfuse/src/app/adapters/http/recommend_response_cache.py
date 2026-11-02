"""Pre-admission response caching for the v1 recommendation route."""

from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any, Callable

from fastapi import HTTPException, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.application.agent_search_cache import AgentSearchResultCache
from app.bootstrap.search_capacity import MAX_SEARCH_BODY_BYTES, _get_search_metrics
from app.domain.models.bot_recommendation import (
    BotRecommendationRequest,
    BotRecommendationResponse,
)
from app.infra.config.feature_flags import FeatureFlags
from app.adapters.http.shared_response_cache_reader import (
    BoundedSharedResponseCacheReader,
    DEFAULT_L2_READ_CONCURRENCY,
)
from app.adapters.http.response_singleflight import ResponseBurstSingleflight
from app.adapters.http.shared_response_cache_writer import (
    DEFAULT_L2_WRITE_QUEUE_SIZE,
    DEFAULT_L2_WRITE_WORKERS,
    BoundedSharedResponseCacheWriter,
)
from app.infra.public.observability.shared_response_cache_metrics import (
    record_shared_response_cache_error,
)

RequireAuth = Callable[[Request], None]
TraceIdProvider = Callable[[], str]
V1_RECOMMEND_RESPONSE_CACHE_OPERATION = "v1_recommendation_response"
DEFAULT_L2_TTL_SECONDS = 300.0
DEFAULT_L2_TIMEOUT_SECONDS = 0.05


def build_v1_recommendation_response_cache_key(
    *,
    result_cache: AgentSearchResultCache,
    request: BotRecommendationRequest,
    index_version: int,
) -> str:
    """Build the route-projection cache key from request-effective semantics."""

    explicit_min_score = "min_score" in request.model_fields_set
    legacy_min_score = _legacy_min_score()
    vector_min_score_env = _float_env("BCSFUSE_VECTOR_MIN_SCORE")
    rerank_min_score_env = _float_env("BCSFUSE_RERANK_MIN_SCORE")

    if request.enable_rerank:
        vector_min_score = (
            vector_min_score_env if vector_min_score_env is not None else 0.01
        )
        if explicit_min_score:
            rerank_min_score = request.min_score
        elif rerank_min_score_env is not None:
            rerank_min_score = rerank_min_score_env
        elif legacy_min_score is not None:
            rerank_min_score = legacy_min_score
        else:
            rerank_min_score = 0.01
    else:
        if explicit_min_score:
            vector_min_score = request.min_score
            rerank_min_score = request.min_score
        elif legacy_min_score is not None:
            vector_min_score = legacy_min_score
            rerank_min_score = legacy_min_score
        else:
            vector_min_score = request.min_score
            rerank_min_score = request.min_score

    request_reranker_model = (
        None
        if not request.enable_rerank
        else request.reranker_model or os.getenv("RERANKER_MODEL", "Qwen3-Reranker-4B")
    )

    return result_cache.key(
        V1_RECOMMEND_RESPONSE_CACHE_OPERATION,
        index_version=index_version,
        material={
            "normalized_query": result_cache.normalize_query(request.question),
            "top_k": request.topK,
            "driver_bot_id": request.driver_bot_id,
            "type": request.type,
            "explicit_min_score": explicit_min_score,
            "min_score": request.min_score,
            "vector_min_score": vector_min_score,
            "rerank_min_score": rerank_min_score,
            "expand_factor": request.expand_factor,
            "enable_rerank": request.enable_rerank,
            "reranker_model": request_reranker_model,
            "filters": request.filters,
            "profile_embedding_enabled": FeatureFlags.is_profile_embedding_index_enabled(),
        },
    )


def store_v1_recommendation_response(
    *,
    result_cache: AgentSearchResultCache | None,
    request: BotRecommendationRequest,
    response: BotRecommendationResponse,
) -> bool:
    """Store a successful route projection in the shared bounded cache."""

    if result_cache is None:
        return False
    if request.group_id:
        return False
    if not _cacheable_response(response):
        return False

    index_version = result_cache.current_index_version()
    if index_version is None:
        return False
    key = build_v1_recommendation_response_cache_key(
        result_cache=result_cache,
        request=request,
        index_version=index_version,
    )
    result_cache.store(
        key,
        response.model_dump_json().encode("utf-8"),
        operation="v1_recommendation_response",
    )
    return True


def _cacheable_response(response: BotRecommendationResponse) -> bool:
    metadata = response.metadata
    if metadata.get("semantic_unavailable") is True:
        return False
    if metadata.get("keyword_unavailable") is True:
        return False
    if metadata.get("literal_fallback_used") is True:
        return False
    if metadata.get("fallback_unavailable") is True:
        return False
    if metadata.get("retrieval_unavailable") is True:
        return False
    if metadata.get("candidate_unavailable") is True:
        return False
    if metadata.get("score_source") == "regex_fallback":
        return False
    return True


class RecommendationResponseCacheMiddleware:
    """Answer warmed v1 recommendation projections before capacity admission."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        result_cache: AgentSearchResultCache | None,
        require_auth: RequireAuth,
        metrics: dict[str, Any] | None = None,
        trace_id_provider: TraceIdProvider | None = None,
        shared_response_cache: Any | None = None,
        shared_cache_reader: BoundedSharedResponseCacheReader | None = None,
        shared_cache_read_concurrency: int = DEFAULT_L2_READ_CONCURRENCY,
        shared_cache_writer: BoundedSharedResponseCacheWriter | None = None,
        shared_cache_ttl_seconds: float = DEFAULT_L2_TTL_SECONDS,
        shared_cache_timeout_seconds: float = DEFAULT_L2_TIMEOUT_SECONDS,
        shared_cache_write_queue_size: int = DEFAULT_L2_WRITE_QUEUE_SIZE,
        shared_cache_write_workers: int = DEFAULT_L2_WRITE_WORKERS,
    ) -> None:
        self.app = app
        self._result_cache = result_cache
        self._require_auth = require_auth
        self._metrics = metrics or _get_search_metrics()
        self._trace_id_provider = trace_id_provider or _current_trace_id
        self._shared_response_cache = shared_response_cache
        self._shared_cache_reader = shared_cache_reader
        if shared_response_cache is not None and shared_cache_reader is None:
            self._shared_cache_reader = BoundedSharedResponseCacheReader(
                shared_response_cache=shared_response_cache,
                timeout_seconds=shared_cache_timeout_seconds,
                max_concurrent_reads=shared_cache_read_concurrency,
            )
        self._shared_cache_ttl_seconds = shared_cache_ttl_seconds
        self._shared_cache_timeout_seconds = shared_cache_timeout_seconds
        self._l2_writer = None
        if shared_response_cache is not None:
            self._l2_writer = shared_cache_writer or BoundedSharedResponseCacheWriter(
                shared_response_cache=shared_response_cache,
                operation="recommend",
                ttl_seconds=shared_cache_ttl_seconds,
                timeout_seconds=shared_cache_timeout_seconds,
                queue_size=shared_cache_write_queue_size,
                worker_count=shared_cache_write_workers,
            )
        self._response_burst = ResponseBurstSingleflight()

    async def _coalesce(
        self,
        *,
        coalescing_key: str,
        scope: Scope,
        receive: Receive,
        send: Send,
        request: BotRecommendationRequest,
        started_at: float,
    ) -> None:
        async def execute_downstream(capture_send: Send) -> None:
            recovered_response = self._cached_response(request)
            if recovered_response is not None:
                self._record_preflight_request(200, started_at)
                await recovered_response(scope, receive, capture_send)
                return

            current_key = self._coalescing_key(request)
            shared_response = None
            if current_key is not None:
                index_version = self._result_cache.current_index_version()
                if index_version is not None:
                    shared_payload = await self._shared_payload(
                        current_key, index_version
                    )
                    if shared_payload is not None:
                        shared_response = self._promoted_shared_response(
                            key=current_key,
                            payload=shared_payload,
                        )

            if shared_response is not None:
                self._record_preflight_request(200, started_at)
                await shared_response(scope, receive, capture_send)
                return

            recording_send = None
            if current_key is not None:
                recording_send = self._recording_send(
                    key=current_key,
                    send=capture_send,
                )
            await self.app(
                scope,
                receive,
                recording_send if recording_send is not None else capture_send,
            )

        await self._response_burst.coalesce(
            key=coalescing_key,
            scope=scope,
            receive=receive,
            send=send,
            execute_downstream=execute_downstream,
            on_shared_response=lambda captured: self._record_preflight_request(
                captured.status_code, started_at
            ),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/v1/recommend"
        ):
            await self.app(scope, receive, send)
            return

        (
            hit_response,
            replay_receive,
            outcome,
            started_at,
            missed_request,
        ) = await self._hit_response(scope, receive)

        if hit_response is not None:
            self._record_preflight_request(outcome, started_at)
            await hit_response(scope, replay_receive, send)
            return

        coalescing_key = (
            None
            if missed_request is None or self._result_cache is None
            else self._coalescing_key(missed_request)
        )
        if coalescing_key is None:
            await self.app(scope, replay_receive, send)
            return

        await self._coalesce(
            coalescing_key=coalescing_key,
            scope=scope,
            receive=replay_receive,
            send=send,
            request=missed_request,
            started_at=started_at,
        )

    async def _hit_response(
        self, scope: Scope, receive: Receive
    ) -> tuple[
        Response | None, Receive, int | None, float, BotRecommendationRequest | None
    ]:
        started_at = perf_counter()
        if self._result_cache is None:
            return None, receive, None, started_at, None

        declared_size = _declared_content_length(scope)
        if declared_size is not None and declared_size > MAX_SEARCH_BODY_BYTES:
            return None, receive, None, started_at, None

        replay_receive, messages = await self._bounded_receive(receive)
        if replay_receive is None:
            return None, receive, None, started_at, None

        try:
            parsed = json.loads(_join_messages(messages))
        except (TypeError, ValueError):
            return None, replay_receive, None, started_at, None
        if not isinstance(parsed, dict):
            return None, replay_receive, None, started_at, None

        try:
            request = BotRecommendationRequest.model_validate(parsed)
        except ValueError:
            return None, replay_receive, None, started_at, None
        if request.group_id:
            return None, replay_receive, None, started_at, None

        try:
            self._require_auth(Request(scope, replay_receive))
        except HTTPException as error:
            return (
                Response(
                    status_code=error.status_code,
                    media_type="application/json",
                    content=json.dumps({"detail": error.detail}).encode("utf-8"),
                ),
                replay_receive,
                error.status_code,
                started_at,
                None,
            )

        cached_response = self._cached_response(
            request, record_failure=True
        )
        if cached_response is not None:
            return cached_response, replay_receive, 200, started_at, None
        return None, replay_receive, None, started_at, request

    def _promoted_shared_response(
        self, *, key: str, payload: bytes
    ) -> Response | None:
        assert self._result_cache is not None
        try:
            cached_payload = self._result_cache.store_and_get(
                key, payload, operation="v1_recommendation_response"
            )
        except Exception:
            record_shared_response_cache_error("recommend", stage="promote")
            return None
        if not isinstance(cached_payload, bytes):
            record_shared_response_cache_error("recommend", stage="promote")
            return None
        return self._decode_response(cached_payload)

    def _decode_response(self, payload: bytes) -> Response | None:
        try:
            deserialized: dict[str, Any] = json.loads(payload)
        except (TypeError, ValueError):
            record_shared_response_cache_error("recommend", stage="decode")
            return None
        if not isinstance(deserialized, dict):
            record_shared_response_cache_error("recommend", stage="decode")
            return None
        deserialized["trace_id"] = self._trace_id_provider()
        body = json.dumps(
            deserialized, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return Response(
            status_code=200,
            media_type="application/json",
            content=body,
        )

    async def _shared_payload(self, key: str, index_version: int) -> bytes | None:
        if self._shared_cache_reader is None:
            return None
        try:
            payload = await self._shared_cache_reader.get(
                key,
                index_version=index_version,
                operation="recommend",
            )
        except Exception:
            record_shared_response_cache_error("recommend", stage="read")
            return None
        if not isinstance(payload, bytes):
            return None
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            record_shared_response_cache_error("recommend", stage="decode")
            return None
        if not isinstance(parsed, dict):
            record_shared_response_cache_error("recommend", stage="decode")
            return None
        return payload

    def _recording_send(self, *, key: str, send: Send) -> Send:
        status_code: int | None = None
        body_chunks: list[bytes] = []

        async def recording(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 0))
            elif message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))
            await send(message)
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and status_code == 200
            ):
                self._schedule_l2_publish(
                    key=key,
                    body=b"".join(body_chunks),
                )

        return recording

    def _schedule_l2_publish(self, *, key: str, body: bytes) -> None:
        if self._l2_writer is None or body == b"":
            return
        index_version = self._result_cache.current_index_version()
        if index_version is None:
            return
        try:
            payload = self._result_cache.get_without_metrics(key)
            if not isinstance(payload, bytes):
                return
            self._l2_writer.submit(
                key=key, payload=payload, index_version=index_version
            )
        except Exception:
            record_shared_response_cache_error("recommend", stage="publish")

    def _coalescing_key(self, request: BotRecommendationRequest) -> str | None:
        assert self._result_cache is not None
        try:
            index_version = self._result_cache.current_index_version()
            if index_version is None:
                return None
            return _response_cache_key(self._result_cache, request, index_version)
        except Exception:
            record_shared_response_cache_error("recommend", stage="preflight")
            return None

    def _cached_response(
        self,
        request: BotRecommendationRequest,
        *,
        record_failure: bool = False,
    ) -> Response | None:
        assert self._result_cache is not None
        try:
            index_version = self._result_cache.current_index_version()
            if index_version is None:
                return None
            cached_payload = self._result_cache.get(
                _response_cache_key(self._result_cache, request, index_version),
                operation="v1_recommendation_response",
            )
        except Exception:
            if record_failure:
                record_shared_response_cache_error("recommend", stage="preflight")
            return None
        if not isinstance(cached_payload, bytes):
            return None

        try:
            deserialized: dict[str, Any] = json.loads(cached_payload)
        except (TypeError, ValueError):
            return None
        deserialized["trace_id"] = self._trace_id_provider()
        body = json.dumps(
            deserialized, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return Response(
            status_code=200,
            media_type="application/json",
            content=body,
        )

    def _record_preflight_request(self, outcome: int, started_at: float) -> None:
        labels = {"operation": "recommend", "api_version": "v1"}
        inflight = self._metrics["inflight"].labels(**labels)
        inflight.inc()
        try:
            self._metrics["requests"].labels(
                **labels, outcome=str(outcome)
            ).inc()
            self._metrics["duration"].labels(
                **labels, outcome=str(outcome)
            ).observe(perf_counter() - started_at)
        finally:
            inflight.dec()

    async def _bounded_receive(
        self, receive: Receive
    ) -> tuple[Receive | None, list[Message]]:
        messages: list[Message] = []
        total_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None, messages
            if message["type"] != "http.request":
                continue
            messages.append(message)
            total_bytes += len(message.get("body", b""))
            if total_bytes > MAX_SEARCH_BODY_BYTES:
                if messages and messages[-1].get("more_body", False):
                    messages.append(
                        {"type": "http.request", "body": b"", "more_body": False}
                    )
                return _replayed_receive(messages), messages
            if not message.get("more_body", False):
                return _replayed_receive(messages), messages


def _response_cache_key(
    result_cache: AgentSearchResultCache,
    request: BotRecommendationRequest,
    index_version: int,
) -> str:
    return build_v1_recommendation_response_cache_key(
        result_cache=result_cache,
        request=request,
        index_version=index_version,
    )


def _declared_content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if bytes(name).lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _replayed_receive(messages: list[Message]) -> Receive:
    iterator = iter(messages)
    terminal_sent = False

    async def receive() -> Message:
        nonlocal terminal_sent
        try:
            return next(iterator)
        except StopIteration:
            if not terminal_sent:
                terminal_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

    return receive


def _join_messages(messages: list[Message]) -> bytes:
    return b"".join(message.get("body", b"") for message in messages)


def _legacy_min_score() -> float | None:
    from app.application.utils.drm_config_helper import get_recommend_min_score

    return get_recommend_min_score()


def _float_env(name: str) -> float | None:
    value = os.getenv(name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _current_trace_id() -> str:
    from app.infra.trace_context import get_trace_id

    return get_trace_id()


__all__ = [
    "RecommendationResponseCacheMiddleware",
    "build_v1_recommendation_response_cache_key",
    "store_v1_recommendation_response",
]
