"""TDD contracts for the short-query, small-corpus search fast path.

The current business shape is deliberately simplified (<=10 workers, short
business queries).  This contract locks in the intended performance boundary:
local worker-card matching can serve confident short queries without calling
the model or vector server.  Larger workspaces and quality modes still route
to semantic retrieval so the system can grow later.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from search_core.plugin_api.vector_point import VectorPoint

from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchService,
)
from app.application.services.literal_fallback_maintenance import (
    LiteralFallbackSnapshot,
)
from app.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from app.domain.models.retrieval_mode import RetrievalMode


@dataclass
class RecordingCore:
    embedding_calls: list[str] = field(default_factory=list)
    match_calls: list[Any] = field(default_factory=list)
    recommend_calls: list[Any] = field(default_factory=list)

    def embed(self, text: str) -> list[float]:
        self.embedding_calls.append(text)
        return [0.1]

    def match(self, **kwargs: Any) -> list[Any]:
        self.match_calls.append(kwargs)
        return []

    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        self.recommend_calls.append(kwargs)
        return CandidateRecommendationResponse(
            question=str(kwargs.get("question", "")),
            metadata={},
        )


def _worker_points(count: int) -> list[VectorPoint]:
    topics = (
        "python search",
        "sql tuning",
        "react performance",
        "security hardening",
        "distributed cache",
        "load balancing",
        "authentication",
        "api design",
        "data indexing",
        "observability",
    )
    points: list[VectorPoint] = []
    for index in range(count):
        worker_id = f"worker_{index:02d}"
        topic = topics[index % len(topics)]
        points.append(
            VectorPoint(
                id=f"{worker_id}:default:full",
                vector=[],
                payload={
                    "profile_key": f"{worker_id}:default",
                    "worker_id": worker_id,
                    "profile_id": "default",
                    "profile_type": "default",
                    "short_profile": f"{topic} specialist",
                    "content": f"{topic} expert for this business",
                    "domains": ["backend"],
                    "active_skill_names": [topic.split()[0]],
                    "runtime_state": "online",
                    "availability": "public",
                },
            )
        )
    return points


def _snapshot(count: int = 10) -> LiteralFallbackSnapshot:
    snapshot = LiteralFallbackSnapshot()
    snapshot.upsert(_worker_points(count))
    return snapshot


def _search_command(
    query: str = "python search",
    mode: str = "fast",
    min_score: float = 0.0,
) -> AgentSearchCommand:
    return AgentSearchCommand(
        query=query,
        top_k=3,
        mode=mode,
        min_score=min_score,
    )


def _recommend_command(
    question: str = "python search",
    vector_min_score: float = 0.01,
) -> AgentRecommendationCommand:
    return AgentRecommendationCommand(
        question=question,
        top_k=3,
        enable_rerank=False,
        reranker_model=None,
        expand_factor=1,
        vector_min_score=vector_min_score,
        rerank_min_score=None,
        min_score_explicit=False,
    )


def test_short_query_uses_local_small_scope_for_search() -> None:
    snapshot = _snapshot(10)
    core = RecordingCore()

    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(service.search(_search_command()))

    assert core.embedding_calls == []
    assert core.match_calls == []
    assert outcome.results
    assert outcome.metadata["small_scope_used"] is True
    assert outcome.metadata["candidate_source"] == "small_scope_snapshot"
    assert outcome.metadata["score_source"] == "small_scope_snapshot"
    assert outcome.config_applied["small_scope_used"] is True


def test_short_question_uses_local_small_scope_for_recommendation() -> None:
    snapshot = _snapshot(10)
    core = RecordingCore()

    service = AgentSearchService(
        embedding_generator=core,
        candidate_recommendation_service=core,
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(service.recommend(_recommend_command()))

    assert core.embedding_calls == []
    assert core.recommend_calls == []
    assert outcome.response.recommendations
    response_metadata = outcome.response.metadata
    assert response_metadata["small_scope_used"] is True
    assert response_metadata["candidate_source"] == "small_scope_snapshot"


def test_explicit_high_score_requirement_bypasses_small_scope() -> None:
    snapshot = _snapshot(10)
    core = RecordingCore()

    search_service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        local_snapshot=snapshot,
    )
    search_outcome = asyncio.run(
        search_service.search(
            _search_command(
                query="python search engine",
                min_score=0.80,
            )
        )
    )

    assert search_outcome.metadata.get("small_scope_used") is not True
    assert core.match_calls != []

    recommend_service = AgentSearchService(
        embedding_generator=core,
        candidate_recommendation_service=core,
        local_snapshot=snapshot,
    )
    recommend_outcome = asyncio.run(
        recommend_service.recommend(
            _recommend_command(
                question="python search engine",
                vector_min_score=0.80,
            )
        )
    )

    assert (
        recommend_outcome.response.metadata.get("small_scope_used")
        is not True
    )
    assert core.recommend_calls != []


def test_long_query_does_not_use_small_scope() -> None:
    long_query = "business analysis " + "python search " * 10
    snapshot = _snapshot(10)
    core = RecordingCore()

    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(service.search(_search_command(long_query)))

    assert outcome.metadata.get("small_scope_used") is not True
    assert core.embedding_calls != []


def test_large_workspace_does_not_use_small_scope() -> None:
    snapshot = _snapshot(21)
    core = RecordingCore()

    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(service.search(_search_command()))

    assert outcome.metadata.get("small_scope_used") is not True
    assert core.embedding_calls != []


def test_accurate_mode_uses_semantic_not_small_scope() -> None:
    snapshot = _snapshot(10)
    core = RecordingCore()

    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(service.search(_search_command(mode="accurate")))

    assert outcome.metadata.get("small_scope_used") is not True
    assert core.embedding_calls != []


def test_fast_low_confidence_small_scope_falls_back_to_vector() -> None:
    snapshot = _snapshot(10)
    calls: dict[str, list[Any]] = {"embedding": [], "match": []}

    class VectorCore:
        def embed(self, text: str) -> list[float]:
            calls["embedding"].append(text)
            return [0.9]

        def match(self, **kwargs: Any) -> list[Any]:
            calls["match"].append(kwargs)
            return [{"profile_key": "worker_vector:default", "score": 0.9}]

    service = AgentSearchService(
        embedding_generator=VectorCore(),
        vector_match_service=VectorCore(),
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(
        service.search(
            _search_command(
                query="python redis",
                min_score=0.80,
            )
        )
    )

    assert calls["embedding"] == ["python redis"]
    assert len(calls["match"]) == 1
    assert outcome.results[0]["profile_key"] == "worker_vector:default"
    assert outcome.metadata["small_scope_attempted"] is True
    assert outcome.metadata["small_scope_used"] is False
    assert outcome.metadata["small_scope_rejected"] is True
    assert outcome.metadata["small_scope_reject_reason"] == "low_score_or_threshold"
    assert outcome.metadata["small_scope_best_score"] == 0.5
    assert outcome.metadata["small_scope_fallback_to_vector"] is True
    assert outcome.metadata.get("candidate_source") != "small_scope_snapshot"
    assert outcome.config_applied["small_scope_attempted"] is True
    assert outcome.config_applied["small_scope_used"] is False


def test_fast_empty_small_scope_falls_back_to_vector() -> None:
    snapshot = _snapshot(10)
    calls: dict[str, list[Any]] = {"embedding": [], "match": []}

    class VectorCore:
        def embed(self, text: str) -> list[float]:
            calls["embedding"].append(text)
            return [0.9]

        def match(self, **kwargs: Any) -> list[Any]:
            calls["match"].append(kwargs)
            return [{"profile_key": "worker_vector:default", "score": 0.9}]

    service = AgentSearchService(
        embedding_generator=VectorCore(),
        vector_match_service=VectorCore(),
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(
        service.search(_search_command(query="xylophone nebula gluten"))
    )

    assert calls["embedding"] == ["xylophone nebula gluten"]
    assert len(calls["match"]) == 1
    assert outcome.results
    assert outcome.metadata["small_scope_attempted"] is True
    assert outcome.metadata["small_scope_used"] is False
    assert outcome.metadata["small_scope_rejected"] is True
    assert outcome.metadata["small_scope_reject_reason"] == "empty"
    assert outcome.metadata["small_scope_best_score"] == 0.0
    assert outcome.metadata["small_scope_fallback_to_vector"] is True
    assert outcome.metadata["status"] == "ok"
    assert outcome.metadata["reasons"] == []


def test_confident_small_scope_publishes_attempt_without_fallback() -> None:
    snapshot = _snapshot(10)
    core = RecordingCore()

    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(service.search(_search_command()))

    assert core.embedding_calls == []
    assert outcome.metadata["small_scope_attempted"] is True
    assert outcome.metadata["small_scope_used"] is True
    assert outcome.metadata["small_scope_rejected"] is False
    assert "small_scope_fallback_to_vector" not in outcome.metadata


def test_low_confidence_recommendation_small_scope_falls_back_to_vector() -> None:
    snapshot = _snapshot(10)
    calls: dict[str, list[Any]] = {"embedding": [], "recommend": []}

    class VectorCore:
        def embed(self, text: str) -> list[float]:
            calls["embedding"].append(text)
            return [0.9]

        def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
            calls["recommend"].append(kwargs)
            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                metadata={"candidate_source": "vector"},
                recommendations=[
                    CandidateRecommendation(
                        profile_key="worker_vector:default",
                        worker_id="worker_vector",
                        score=0.9,
                    )
                ],
            )

    service = AgentSearchService(
        embedding_generator=VectorCore(),
        candidate_recommendation_service=VectorCore(),
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(
        service.recommend(
            _recommend_command(
                question="python redis",
                vector_min_score=0.80,
            )
        )
    )

    assert calls["embedding"] == ["python redis"]
    assert len(calls["recommend"]) == 1
    assert outcome.response.recommendations
    response_metadata = outcome.response.metadata
    assert response_metadata["small_scope_attempted"] is True
    assert response_metadata["small_scope_used"] is False
    assert response_metadata["small_scope_rejected"] is True
    assert response_metadata["small_scope_reject_reason"] == "low_score_or_threshold"
    assert response_metadata["small_scope_best_score"] == 0.5
    assert response_metadata["small_scope_fallback_to_vector"] is True
    assert response_metadata["status"] == "ok"
    assert response_metadata["reasons"] == []
    assert response_metadata["candidate_source"] == "vector"


def test_small_scope_rejection_survives_terminal_local_snapshot() -> None:
    snapshot = _snapshot(10)
    embedding_calls: list[str] = []

    class EmptyVectorCore:
        def embed(self, text: str) -> list[float]:
            embedding_calls.append(text)
            return [0.9]

        def match(self, **kwargs: Any) -> list[Any]:
            return []

    service = AgentSearchService(
        embedding_generator=EmptyVectorCore(),
        vector_match_service=EmptyVectorCore(),
        local_snapshot=snapshot,
    )
    outcome = asyncio.run(
        service.search(
            _search_command(
                query="python redis",
                min_score=0.80,
            )
        )
    )

    assert embedding_calls == ["python redis"]
    assert outcome.results
    assert outcome.metadata["small_scope_attempted"] is True
    assert outcome.metadata["small_scope_used"] is False
    assert outcome.metadata["small_scope_rejected"] is True
    assert outcome.metadata["small_scope_fallback_to_vector"] is True
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata["status"] == "degraded"
    assert "local_snapshot_used" in outcome.metadata["reasons"]
