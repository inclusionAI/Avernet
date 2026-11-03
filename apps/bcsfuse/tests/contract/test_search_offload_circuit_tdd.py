"""TDD for routing outage offload through a dedicated worker pool."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.application.services.retrieval_dependency_circuit import (
    RetrievalDependencyCircuit,
)


@pytest.fixture(autouse=True)
def _restore_worker_pools(monkeypatch: pytest.MonkeyPatch):
    from app.adapters.http import search_threadpool

    monkeypatch.setattr(
        search_threadpool,
        "_search_thread_limiter",
        search_threadpool.to_thread.CapacityLimiter(64),
    )
    monkeypatch.setattr(
        search_threadpool,
        "_local_fallback_thread_limiter",
        search_threadpool.to_thread.CapacityLimiter(64),
    )
    yield
    search_threadpool.configure_search_thread_capacity(64)
    search_threadpool.configure_local_fallback_thread_capacity(64)


def test_open_outage_offload_uses_dedicated_local_workers() -> None:
    from app.adapters.http.search_threadpool import (
        configure_local_fallback_thread_capacity,
        configure_search_thread_capacity,
        route_search_offload,
        run_search_in_threadpool,
    )

    configure_search_thread_capacity(1)
    configure_local_fallback_thread_capacity(1)
    release = threading.Event()
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)
    offload = route_search_offload(circuit)

    def occupy_semantic_worker() -> None:
        release.wait(2)

    async def scenario() -> tuple[float, object]:
        semantic_work = asyncio.create_task(
            run_search_in_threadpool(occupy_semantic_worker)
        )
        await asyncio.sleep(0.05)
        circuit.record_outage()
        started = time.perf_counter()
        local_result = await asyncio.wait_for(
            offload(lambda: "local-worker"), timeout=0.3
        )
        elapsed = time.perf_counter() - started
        release.set()
        await semantic_work
        return elapsed, local_result

    elapsed, local_result = asyncio.run(scenario())

    assert local_result == "local-worker"
    assert elapsed < 0.2


def test_closed_circuit_offload_uses_the_bounded_search_pool() -> None:
    from app.adapters.http.search_threadpool import (
        configure_local_fallback_thread_capacity,
        configure_search_thread_capacity,
        route_search_offload,
    )

    configure_search_thread_capacity(1)
    configure_local_fallback_thread_capacity(1)
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)
    offload = route_search_offload(circuit)

    async def scenario() -> object:
        return await offload(lambda: "semantic-worker")

    assert asyncio.run(scenario()) == "semantic-worker"


def test_composition_exposes_the_circuit_aware_offload() -> None:
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class StartupProvider:
        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    app = create_bcsfuse_app(
        ApplicationContext(
            mode="runtime",
            startup_profile="opensource",
            registry=registry,
        )
    )

    assert app.state.retrieval_dependency_offload is not None


def _snapshot_with_fallback_profile():
    from search_core.plugin_api.vector_point import VectorPoint

    from app.application.services.literal_fallback_maintenance import (
        LiteralFallbackSnapshot,
    )

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
                    "content": "python vector search fallback",
                    "runtime_state": "online",
                    "availability": "public",
                },
            )
        ]
    )
    return snapshot


def test_terminal_search_service_uses_dedicated_local_workers() -> None:
    from app.adapters.http.search_threadpool import (
        configure_local_fallback_thread_capacity,
        configure_search_thread_capacity,
        route_search_offload,
        run_search_in_threadpool,
    )
    from app.application.agent_search_service import (
        AgentSearchCommand,
        AgentSearchService,
    )

    class AlwaysFailingMatch:
        def match(self, **kwargs):
            raise RuntimeError("semantic worker unavailable")

    class NeverCalledEmbedding:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedding worker unavailable")

    configure_search_thread_capacity(1)
    configure_local_fallback_thread_capacity(1)
    release = threading.Event()
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)
    circuit.record_outage()
    service = AgentSearchService(
        embedding_generator=NeverCalledEmbedding(),
        vector_match_service=AlwaysFailingMatch(),
        local_snapshot=_snapshot_with_fallback_profile(),
        retrieval_dependency_circuit=circuit,
        offload=route_search_offload(circuit),
    )

    def occupy_semantic_worker() -> None:
        release.wait(2)

    async def scenario():
        semantic_work = asyncio.create_task(
            run_search_in_threadpool(occupy_semantic_worker)
        )
        await asyncio.sleep(0.05)
        outcome = await asyncio.wait_for(
            service.search(
                AgentSearchCommand(query="python vector search", top_k=1)
            ),
            timeout=0.5,
        )
        release.set()
        await semantic_work
        return outcome

    outcome = asyncio.run(scenario())

    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata["retrieval_dependency_failfast"] is True
