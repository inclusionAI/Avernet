"""Ten serving-capacity and availability contracts.

The first three tests are intentionally sharper than earlier per-component tests:
runtime storage must fail closed, a transport adapter must never invent a local
Qdrant instance, and optional L2 workers must be process-owned and closed.
The remaining tests are the integrated acceptance gate for behavior that was
delivered incrementally but must continue to ship together.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.application.agent_search_cache import AgentSearchResultCache
from app.adapters.http.response_singleflight import ResponseBurstSingleflight


class RateLimitBackend:
    dimension = 4

    def embed(self, text: str) -> list[float]:
        error = RuntimeError("model quota")
        error.status_code = 429  # type: ignore[attr-defined]
        raise error


class RateLimitContainer:
    def __init__(self) -> None:
        self.upper = RateLimitBackend()

    def observe(self):
        return ("available", 0.0)

    def reserve(self, *args: Any, **kwargs: Any):
        return (True, 0.0, None)

    def mark_exhausted(self, *args: Any, **kwargs: Any):
        return (None, self._now + 60.0)

    _now = 0.0


class SharedStore:
    def __init__(self) -> None:
        self.reads = 0
        self.writes = 0

    def get(self, key: str, *, index_version: int) -> bytes | None:
        self.reads += 1
        return None

    def store(
        self,
        key: str,
        payload: bytes,
        *,
        index_version: int,
        ttl_seconds: float,
    ) -> bool:
        self.writes += 1
        return True


def cache(version: int = 7) -> AgentSearchResultCache:
    return AgentSearchResultCache(
        index_version_provider=lambda: version,
        max_entries=10,
        ttl_seconds=30.0,
    )


def test_upgrade_1_runtime_qdrant_must_be_shared_server(monkeypatch) -> None:
    from app.bootstrap.opensource import resolve_runtime_qdrant_connections

    monkeypatch.setenv("QDRANT_MODE", "local")
    monkeypatch.setenv("QDRANT_LOCAL_PATH", "/tmp/must-not-be-used")

    try:
        resolve_runtime_qdrant_connections()
    except ValueError as error:
        assert "QDRANT_MODE=server" in str(error)
    else:
        raise AssertionError("runtime composition accepted local Qdrant")


def test_upgrade_2_keyword_cluster_may_split_but_stays_server(monkeypatch) -> None:
    from app.bootstrap.opensource import resolve_runtime_qdrant_connections

    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("QDRANT_URL", "http://dense.example.test:6333")
    monkeypatch.setenv("QDRANT_KEYWORD_URL", "http://keyword.example.test:6333")
    dense, keyword = resolve_runtime_qdrant_connections()

    assert dense.mode == "server"
    assert dense.url == "http://dense.example.test:6333"
    assert keyword.mode == "server"
    assert keyword.url == "http://keyword.example.test:6333"


def test_upgrade_3_missing_vector_store_never_creates_local_qdrant(
    monkeypatch, tmp_path
) -> None:
    from app.adapters.http.dependencies import fusion_dependencies

    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "must-not-exist"))

    try:
        fusion_dependencies.reset_fusion_services()
        fusion_dependencies.set_app_context(None)
        try:
            fusion_dependencies._get_vector_match_service()
        except RuntimeError as error:
            assert "VECTOR_STORE_REQUIRED" in str(error)
        else:
            raise AssertionError("adapter created a local Qdrant fallback")
    finally:
        fusion_dependencies.reset_fusion_services()
        fusion_dependencies.set_app_context(None)

    assert not (tmp_path / "must-not-exist").exists()


def test_upgrade_4_embedding_429_is_shared_quota_degradation() -> None:
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
        EmbeddingQuotaExhaustedError,
    )

    provider = CircuitBreakerEmbeddingProvider(
        RateLimitBackend(),
        capacity_store=RateLimitContainer(),
        quota_recovery_seconds=60.0,
    )

    try:
        provider.embed("unique cold query")
    except EmbeddingQuotaExhaustedError as error:
        assert error.reason_code == "embedding_quota_exhausted"
        assert error.status_code == 429
        assert error.retry_after_seconds > 0
    else:
        raise AssertionError("429 was hidden as a successful embedding")


def test_upgrade_5_dependency_failure_still_returns_bounded_snapshot() -> None:
    from app.application.agent_search_service import (
        AgentSearchCommand,
        AgentSearchService,
    )
    from app.application.services.agent_search_execution_budget import (
        AgentSearchExecutionBudget,
    )
    from app.application.services.literal_fallback_maintenance import (
        LiteralFallbackSnapshot,
    )
    from search_core.plugin_api.vector_point import VectorPoint

    snapshot = LiteralFallbackSnapshot()
    snapshot.upsert(
        [
            VectorPoint(
                id="worker:default:full",
                vector=[],
                payload={
                    "profile_key": "worker:default",
                    "worker_id": "worker",
                    "profile_id": "default",
                    "profile_type": "default",
                    "source_root": "local-snapshot",
                    "short_profile": "Python search",
                    "content": "Python vector retrieval",
                    "runtime_state": "online",
                    "availability": "public",
                },
            )
        ]
    )

    class FailingEmbedding:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("semantic unavailable")

    class FailingSearch:
        def match(self, **kwargs: Any) -> list[Any]:
            raise RuntimeError("qdrant unavailable")

    service = AgentSearchService(
        embedding_generator=FailingEmbedding(),
        vector_match_service=FailingSearch(),
        local_snapshot=snapshot,
        execution_budget=AgentSearchExecutionBudget(
            embedding_timeout_seconds=0.05,
            retrieval_timeout_seconds=0.05,
            local_snapshot_timeout_seconds=0.05,
            total_timeout_seconds=0.15,
        ),
    )
    outcome = asyncio.run(
        service.search(
            AgentSearchCommand(query="python", top_k=3, mode="accurate")
        )
    )

    assert outcome.results
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata["status"] == "degraded"


def test_upgrade_6_result_cache_is_index_version_bound(monkeypatch) -> None:
    from app.adapters.http.search_response_cache import (
        build_v1_search_response_cache_key,
    )
    from app.bootstrap.oss_business_routes import SearchRequestOSS

    result_cache = AgentSearchResultCache(
        index_version_provider=lambda: 7,
        max_entries=10,
        ttl_seconds=30.0,
    )
    request = SearchRequestOSS(query=" 支付 risk ", top_k=3)
    key_v7 = build_v1_search_response_cache_key(
        result_cache=result_cache, request=request, index_version=7
    )
    key_v8 = build_v1_search_response_cache_key(
        result_cache=result_cache, request=request, index_version=8
    )
    normalized = build_v1_search_response_cache_key(
        result_cache=result_cache,
        request=SearchRequestOSS(query="支付  risk", top_k=3),
        index_version=7,
    )

    assert key_v7 == normalized
    assert key_v7 != key_v8


async def _consume_body(receive: Any) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.request" and not message.get("more_body", False):
            return


def test_upgrade_7_response_singleflight_coalesces_identical_misses() -> None:
    class Downstream:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1
            await _consume_body(receive)
            self.started.set()
            await self.release.wait()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b"{}"})

    downstream = Downstream()
    singleflight = ResponseBurstSingleflight()

    def receive_for(body: bytes, loops: list[dict[str, Any]]) -> Any:
        async def receive() -> dict[str, Any]:
            data = loops.pop(0)
            return data

        loops.append({"type": "http.request", "body": body, "more_body": False})
        return receive

    async def call() -> None:
        received: list[dict[str, Any]] = []
        receive = receive_for(b"{}", received)
        sent: list[dict[str, Any]] = []

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await singleflight.coalesce(
            key="same-key",
            scope={"type": "http"},
            receive=receive,
            send=send,
            execute_downstream=lambda capture_send: downstream(
                {"type": "http"}, receive, capture_send
            ),
        )

    async def scenario() -> None:
        tasks = [asyncio.create_task(call()) for _ in range(3)]
        await downstream.started.wait()
        await asyncio.sleep(0)
        assert singleflight.in_flight_keys == 1
        downstream.release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert downstream.calls == 1


def test_upgrade_8_l2_is_optional_and_fails_open() -> None:
    from app.adapters.http.shared_response_cache_reader import (
        BoundedSharedResponseCacheReader,
    )

    class UnavailableSharedStore:
        def get(self, key: str, *, index_version: int) -> bytes | None:
            raise RuntimeError("L2 unavailable")

        def store(self, *args: Any, **kwargs: Any) -> bool:
            return True

    reader = BoundedSharedResponseCacheReader(
        shared_response_cache=UnavailableSharedStore(),
        timeout_seconds=0.01,
        max_concurrent_reads=1,
    )

    async def scenario() -> None:
        payload = await reader.get("key", index_version=7, operation="search")
        assert payload is None
        await reader.close()

    asyncio.run(scenario())

def test_upgrade_9_runtime_composes_explicit_shared_qdrant(monkeypatch) -> None:
    from app.bootstrap.opensource import resolve_runtime_qdrant_connections

    monkeypatch.delenv("QDRANT_MODE", raising=False)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.example.test:6333")
    dense, keyword = resolve_runtime_qdrant_connections()

    assert dense.mode == keyword.mode == "server"
    assert dense.url == keyword.url == "http://qdrant.example.test:6333"


def test_upgrade_10_l2_writers_are_process_owned_and_closed() -> None:
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class Startup:
        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", Startup())
    shared_store = SharedStore()
    registry.register("shared_response_cache", shared_store)
    context = ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )
    app = create_bcsfuse_app(context)

    writers = app.state.shared_cache_writers
    assert len(writers) == 2
    assert {writer.operation for writer in writers} == {"search", "recommend"}
    assert all(writer.is_closed is False for writer in writers)

    async def shutdown() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(shutdown())
    assert all(writer.is_closed is True for writer in writers)
