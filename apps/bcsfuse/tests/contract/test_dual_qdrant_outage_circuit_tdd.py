"""TDD: dual Qdrant outage must use a bounded circuit and local fallback."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from search_core.providers.embedding import EmbeddingDimensionContractError

from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchService,
)
from app.application.services.retrieval_dependency_circuit import (
    RetrievalDependencyCircuit,
)
from app.domain.models.candidate_recommendation import CandidateRecommendationResponse
from app.domain.models.retrieval_mode import RetrievalMode
from search_core.plugin_api.vector_point import VectorPoint
from app.application.services.literal_fallback_maintenance import (
    LiteralFallbackSnapshot,
)


def _snapshot() -> LiteralFallbackSnapshot:
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
                    "short_profile": "Reserved outage fallback worker",
                    "content": "dual qdrant outage local fallback",
                    "domains": ["backend"],
                    "active_skill_names": ["search"],
                    "runtime_state": "online",
                    "availability": "public",
                },
            )
        ]
    )
    return snapshot


class MustNotRunEmbedding:
    def __init__(self) -> None:
        self.called = False

    def embed(self, text: str) -> list[float]:
        self.called = True
        raise AssertionError("embedding must not be called during dual outage fail-fast")


class MustNotRunMatch:
    def __init__(self) -> None:
        self.called = False

    def match(self, **kwargs: Any) -> list[Any]:
        self.called = True
        raise AssertionError("qdrant match must not be called during dual outage fail-fast")


class MustNotRunRecommendation:
    def __init__(self) -> None:
        self.called = False

    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        self.called = True
        raise AssertionError("candidate service must not be called during dual outage fail-fast")


def _search_command(query: str = "payment specialist") -> AgentSearchCommand:
    return AgentSearchCommand(query=query, top_k=3)


def _recommend_command() -> AgentRecommendationCommand:
    return AgentRecommendationCommand(
        question="payment specialist",
        top_k=3,
        enable_rerank=False,
        reranker_model=None,
        expand_factor=2,
        vector_min_score=0.01,
        rerank_min_score=None,
        min_score_explicit=False,
    )


def _outage_circuit() -> RetrievalDependencyCircuit:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30.0)
    circuit.record_outage()
    return circuit


def test_search_dual_outage_skips_embedding_and_qdrant() -> None:
    embedding = MustNotRunEmbedding()
    match = MustNotRunMatch()
    service = AgentSearchService(
        embedding_generator=embedding,
        vector_match_service=match,
        local_snapshot=_snapshot(),
        retrieval_dependency_circuit=_outage_circuit(),
    )

    import asyncio

    outcome = asyncio.run(service.search(_search_command()))

    assert embedding.called is False
    assert match.called is False
    assert outcome.results
    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["dense_qdrant_available"] is False
    assert outcome.metadata["keyword_qdrant_available"] is False
    assert outcome.metadata["semantic_unavailable"] is True
    assert outcome.metadata["semantic_reason_code"] == "retrieval_dependency_unavailable"
    assert outcome.metadata["retrieval_unavailable"] is True
    assert outcome.metadata["keyword_unavailable"] is True
    assert outcome.metadata["retrieval_dependency_failfast"] is True
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata.get("fallback_unavailable") is not True
    assert outcome.metadata["status"] == "degraded"


def test_recommend_dual_outage_skips_candidate_and_qdrant() -> None:
    candidate = MustNotRunRecommendation()
    service = AgentSearchService(
        candidate_recommendation_service=candidate,
        local_snapshot=_snapshot(),
        retrieval_dependency_circuit=_outage_circuit(),
    )

    import asyncio

    outcome = asyncio.run(service.recommend(_recommend_command()))

    assert candidate.called is False
    assert outcome.response.recommendations
    assert outcome.response.recommendations[0].profile_key == "worker:default"
    assert outcome.response.metadata["dense_qdrant_available"] is False
    assert outcome.response.metadata["keyword_qdrant_available"] is False
    assert outcome.response.metadata["semantic_unavailable"] is True
    assert outcome.response.metadata["candidate_unavailable"] is True
    assert outcome.response.metadata["keyword_unavailable"] is True
    assert outcome.response.metadata["retrieval_dependency_failfast"] is True
    assert outcome.response.metadata["local_snapshot_used"] is True
    assert outcome.response.metadata["candidate_source"] == "local_snapshot"
    assert outcome.response.metadata.get("fallback_unavailable") is not True


def test_outage_circuit_bounds_concurrent_recovery_probes() -> None:
    current_time = 100.0
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30.0, clock=lambda: current_time)
    circuit.record_outage()

    assert circuit.should_fail_fast() is True

    current_time += 31.0
    normal_slots = [circuit.should_fail_fast() for _ in range(20)]
    simultaneous_normal = sum(value is False for value in normal_slots)

    assert simultaneous_normal == 1
    circuit.record_outage()
    assert circuit.should_fail_fast() is True


def test_outage_circuit_recovers_after_healthy_probe() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=0.01)
    circuit.record_outage()
    assert circuit.should_fail_fast() is True

    time.sleep(0.02)
    assert circuit.should_fail_fast() is False
    circuit.record_healthy()
    assert circuit.should_fail_fast() is False


def test_outage_circuit_concurrent_observation_is_thread_safe() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30.0)
    circuit.record_outage()
    errors: list[BaseException] = []

    def observer() -> None:
        try:
            circuit.record_healthy()
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=observer) for _ in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert circuit.should_fail_fast() is False


def test_partial_qdrant_outage_does_not_enter_dual_outage_failfast() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30.0)
    circuit.observe_retrieval(
        {
            "semantic_unavailable": True,
            "keyword_unavailable": False,
            "keyword_search_used": True,
        }
    )
    assert circuit.should_fail_fast() is False


def test_dual_outage_is_recorded_after_normal_retrieval_path() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30.0)
    circuit.observe_retrieval(
        {
            "semantic_unavailable": True,
            "keyword_unavailable": True,
            "literal_fallback_used": True,
        }
    )
    assert circuit.should_fail_fast() is True


def test_dual_outage_terminal_fallback_search_is_offloaded() -> None:
    class RecordingOffload:
        def __init__(self) -> None:
            self.called = False

        async def __call__(self, operation):
            self.called = True
            return operation()

    offload = RecordingOffload()
    service = AgentSearchService(
        local_snapshot=_snapshot(),
        retrieval_dependency_circuit=_outage_circuit(),
        offload=offload,
    )

    outcome = asyncio.run(service.search(_search_command()))

    assert outcome.results
    assert outcome.metadata["retrieval_dependency_failfast"] is True
    assert offload.called is True


def test_dual_outage_terminal_fallback_recommendation_is_offloaded() -> None:
    class RecordingOffload:
        def __init__(self) -> None:
            self.called = False

        async def __call__(self, operation):
            self.called = True
            return operation()

    offload = RecordingOffload()
    service = AgentSearchService(
        candidate_recommendation_service=MustNotRunRecommendation(),
        local_snapshot=_snapshot(),
        retrieval_dependency_circuit=_outage_circuit(),
        offload=offload,
    )

    outcome = asyncio.run(service.recommend(_recommend_command()))

    assert outcome.response.recommendations
    assert outcome.response.metadata["retrieval_dependency_failfast"] is True
    assert offload.called is True


def test_partial_or_unknown_observation_does_not_close_terminal_outage() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30.0)
    circuit.record_outage()

    circuit.observe_retrieval({"semantic_unavailable": True})
    assert circuit.outaged is True

    circuit.observe_retrieval(
        {
            "semantic_unavailable": True,
            "keyword_unavailable": True,
            "dense_qdrant_available": False,
        }
    )
    assert circuit.outaged is True

    circuit.observe_retrieval(
        {
            "dense_qdrant_available": True,
            "keyword_qdrant_available": True,
        }
    )
    assert circuit.outaged is False
