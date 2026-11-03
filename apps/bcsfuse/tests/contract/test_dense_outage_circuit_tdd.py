"""TDD: a dense outage must fail fast to keyword without losing keyword service."""

from __future__ import annotations

import asyncio

import pytest

from app.application.agent_search_service import (
    AgentSearchCommand,
    AgentSearchService,
)
from app.application.services.retrieval_dependency_circuit import (
    RetrievalDependencyCircuit,
)
from app.application.services.search_degradation_reasons import degraded_reasons
from app.application.services.worker_vector_match_types import MatchResult
from app.bootstrap.retrieval_dependency_health import (
    RetrievalDependencyHealthMonitor,
)


def test_dense_outage_is_independent_from_the_terminal_dual_outage() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)

    circuit.record_dense_outage()

    assert circuit.dense_outaged is True
    assert circuit.outaged is False
    assert circuit.should_fail_fast() is False

    circuit.record_dense_healthy()

    assert circuit.dense_outaged is False
    assert circuit.outaged is False

    # A previous transient dual outage must not remain terminal once keyword
    # becomes healthy again, even while dense is still unavailable.
    circuit.record_outage()
    circuit.record_dense_outage()

    assert circuit.dense_outaged is True
    assert circuit.outaged is False


def test_request_level_dense_failure_opens_dense_only_bypass() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)

    circuit.observe_retrieval(
        {
            "semantic_unavailable": True,
            "dense_qdrant_available": False,
            "keyword_qdrant_available": True,
        }
    )

    assert circuit.dense_outaged is True
    assert circuit.outaged is False
    assert circuit.should_fail_fast() is False


def test_health_probe_records_a_dense_only_failure_as_dense_bypass() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)
    states = [
        (False, {"dense_qdrant_ready": False, "keyword_qdrant_ready": True}),
        (True, {"dense_qdrant_ready": True, "keyword_qdrant_ready": True}),
    ]

    async def probe():
        return states.pop(0)

    monitor = RetrievalDependencyHealthMonitor(
        readiness_probe=probe,
        circuit=circuit,
        probe_interval_seconds=1.0,
    )

    asyncio.run(monitor.observe_once_with_timeout(timeout_seconds=0.1))
    assert circuit.dense_outaged is True
    assert circuit.outaged is False

    asyncio.run(monitor.observe_once_with_timeout(timeout_seconds=0.1))
    assert circuit.dense_outaged is False
    assert circuit.outaged is False


def test_health_probe_maps_available_fields_from_real_readiness_metadata() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)

    async def probe():
        return False, {
            "dense_qdrant_available": False,
            "keyword_qdrant_available": True,
        }

    monitor = RetrievalDependencyHealthMonitor(
        readiness_probe=probe,
        circuit=circuit,
        probe_interval_seconds=1.0,
    )

    asyncio.run(monitor.observe_once_with_timeout(timeout_seconds=0.1))

    assert circuit.dense_outaged is True
    assert circuit.outaged is False


def test_search_skips_embedding_and_dense_work_during_dense_bypass() -> None:
    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)
    circuit.record_dense_outage()

    class MustNotBeCalledEmbedding:
        def embed(self, text: str) -> list[float]:  # pragma: no cover
            raise AssertionError("embedding must not be called")

    class KeywordMatch:
        def __init__(self) -> None:
            self.called = False

        def match(self, **kwargs):
            self.called = True
            assert kwargs["query_embedding"] == []
            kwargs["runtime_config"]["_retrieval"] = {
                "score_source": "hybrid_rrf",
                "keyword_search_used": True,
            }
            return [
                MatchResult(
                profile_key="worker:default", metadata={}, score=1.0
            ),
            ]

    match = KeywordMatch()
    service = AgentSearchService(
        embedding_generator=MustNotBeCalledEmbedding(),
        vector_match_service=match,
        retrieval_dependency_circuit=circuit,
    )

    outcome = asyncio.run(
        service.search(
            AgentSearchCommand(query="python vector retrieval", top_k=1)
        )
    )

    assert match.called is True
    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["dense_failfast"] is True
    assert outcome.metadata["dense_unavailable"] is True
    assert outcome.metadata["semantic_unavailable"] is False
    assert outcome.metadata["candidate_source"] == "keyword_fallback"
    assert (
        outcome.config_applied["_retrieval"]["candidate_source"]
        == "keyword_fallback"
    )
    assert "dense_failfast" in degraded_reasons(outcome.metadata)


def test_agent_recommendation_dense_bypass_reaches_candidate_service():
    from app.application.agent_search_service import (
        AgentRecommendationCommand,
        AgentSearchService,
    )
    from app.domain.models.candidate_recommendation import (
        CandidateRecommendation,
        CandidateRecommendationResponse,
    )

    class Candidate:
        def __init__(self) -> None:
            self.called = False

        def recommend(self, **kwargs):
            self.called = True
            assert kwargs["runtime_config"]["dense_failfast"] is True
            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode=kwargs["mode"],
                metadata={},
                recommendations=[
                    CandidateRecommendation(
                        profile_key="worker:default",
                        worker_id="worker",
                        score=1.0,
                    )
                ],
                total_candidates=1,
                selected_candidates=1,
            )

    circuit = RetrievalDependencyCircuit(outage_probe_seconds=30)
    circuit.record_dense_outage()
    candidate = Candidate()

    class NeverCalledEmbedding:
        def embed(self, text: str) -> list[float]:  # pragma: no cover
            raise AssertionError("embedding must be skipped during dense bypass")

    service = AgentSearchService(
        embedding_generator=NeverCalledEmbedding(),
        candidate_recommendation_service=candidate,
        retrieval_dependency_circuit=circuit,
    )
    outcome = asyncio.run(
        service.recommend(
            AgentRecommendationCommand(
                question="python vector retrieval",
                top_k=1,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=3,
                vector_min_score=0.01,
                rerank_min_score=0.01,
                min_score_explicit=True,
            )
        )
    )

    assert candidate.called is True
    assert outcome.response.metadata["dense_failfast"] is True
    assert outcome.response.metadata["dense_unavailable"] is True
    assert outcome.response.metadata["semantic_unavailable"] is False
    assert "dense_failfast" in degraded_reasons(outcome.response.metadata)


def test_recommendation_dense_bypass_skips_embedding_and_dense_work(tmp_path):
    from search_core.plugin_api.vector_point import VectorPoint
    from search_core.providers.fallback import RegexFallbackIndex

    from app.application.services.worker_candidate_recommendation_impl import (
        WorkerCandidateRecommendationImpl,
    )
    from app.application.services.worker_vector_match_service import (
        FragmentRetrievalConfig,
        WorkerVectorMatchService,
    )
    from app.domain.models.metadata_record import MetadataRecord
    from app.domain.models.retrieval_mode import RetrievalMode
    from app.infra.config.feature_flags import FeatureFlags
    from app.infra.metadatastores.file_metadata_store_adapter import (
        FileMetadataStoreAdapter,
    )

    class MustNotBeCalledEmbedding:
        def embed(self, text: str) -> list[float]:  # pragma: no cover
            raise AssertionError("embedding must not be called during dense bypass")

    class DenseMustBeSkippedStore:
        def size(self) -> int:
            return 1

        def search(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("dense search must be skipped during dense bypass")

        def text_search(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("local keyword snapshot must absorb dense bypass")

    point = VectorPoint(
        id="worker:default",
        vector=[],
        payload={
            "profile_key": "worker:default",
            "worker_id": "worker",
            "profile_id": "default",
            "content": "python vector search",
            "runtime_state": "online",
            "availability": "public",
        },
    )
    vector_match = WorkerVectorMatchService(
        vector_store=DenseMustBeSkippedStore(),
        metadata_store=FileMetadataStoreAdapter(
            storage_dir=str(tmp_path / "metadata")
        ),
        fallback_index=RegexFallbackIndex([point]),
        fragment_config=FragmentRetrievalConfig(
            enable_fragment_embedding=True,
            reranker_model=None,
        ),
    )
    service = WorkerCandidateRecommendationImpl(
        retrieval_service=object(),
        vector_match_service=vector_match,
        embedding_generator=MustNotBeCalledEmbedding(),
    )

    FeatureFlags.reset()
    FeatureFlags._get_settings()
    response = service.recommend(
        question="python vector search",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        participants=None,
        max_candidates=1,
        runtime_config={"dense_failfast": True},
        vector_min_score=0.01,
        rerank_min_score=0.01,
    )

    assert [item.profile_key for item in response.recommendations] == [
        "worker:default"
    ]
    assert response.metadata["semantic_unavailable"] is False
    assert response.metadata["dense_failfast"] is True
    assert response.metadata["dense_unavailable"] is True
    assert response.metadata["candidate_source"] == "keyword_fallback"
