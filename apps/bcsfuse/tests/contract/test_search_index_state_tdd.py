"""TDD contract for exposing partial index state on search responses."""

from __future__ import annotations

import asyncio
from typing import Any

from app.adapters.http.search_response_projection import build_v1_search_response
from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchOutcome,
    AgentSearchService,
    degraded_reasons,
)
from app.application.services.search_index_state import (
    search_index_state_metadata,
)
from app.bootstrap.oss_business_routes import SearchRequestOSS
from app.bootstrap.index_backfill_status import IndexBackfillStatus
from app.domain.models.candidate_recommendation import CandidateRecommendationResponse
from app.domain.models.retrieval_mode import RetrievalMode


class Registry:
    def __init__(self, vector_store: Any, backfill: Any = None) -> None:
        self.vector_store = vector_store
        self.index_backfill_status = backfill

    def get(self, name: str) -> Any:
        if name == "vector_store":
            return self.vector_store
        if name == "index_backfill_status":
            return self.index_backfill_status
        return None


class VectorStore:
    index_size = 457
    index_version = 12
    index_lag_seconds = 7.5
    index_stale = False
    index_pending_count = 43
    startup_rebuild_failed = False
    index_state_unavailable = False


class Embedding:
    def embed(self, query: str) -> list[float]:
        return [1.0]


class Match:
    def match(self, **kwargs: Any) -> list[Any]:
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                profile_key="worker:default",
                score=0.9,
            )
        ]


def _search_service(index_state: dict[str, Any]) -> AgentSearchService:
    return AgentSearchService(
        embedding_generator=Embedding(),
        vector_match_service=Match(),
        index_state_provider=lambda: index_state,
    )


def test_incomplete_index_is_visible_and_never_reported_healthy() -> None:
    service = _search_service(
        {
            "index_size": 457,
            "index_pending_count": 43,
            "index_complete": False,
            "index_version": 12,
            "index_lag_seconds": 7.5,
            "index_stale": False,
        }
    )

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="writer", top_k=1))
    )

    assert outcome.metadata["index_complete"] is False
    assert outcome.metadata["index_pending_count"] == 43
    assert "index_incomplete" in degraded_reasons(outcome.metadata)
    response = build_v1_search_response(
        SearchRequestOSS(query="writer", top_k=1),
        outcome,
        elapsed_ms=1.0,
    )
    assert response["status"] == "degraded"
    assert response["index_state"] == {
        "index_size": 457,
        "index_pending_count": 43,
        "index_complete": False,
        "index_version": 12,
        "index_lag_seconds": 7.5,
        "index_stale": False,
    }


def test_complete_index_does_not_add_degradation_reasons() -> None:
    service = _search_service({"index_complete": True})

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="writer", top_k=1))
    )

    assert outcome.metadata["index_complete"] is True
    assert "index_incomplete" not in degraded_reasons(outcome.metadata)
    response = build_v1_search_response(
        SearchRequestOSS(query="writer", top_k=1),
        outcome,
        elapsed_ms=1.0,
    )
    assert response["status"] == "ok"
    assert response["index_state"]["index_complete"] is True


def test_recommendation_outcome_carries_the_same_index_state() -> None:
    class CandidateService:
        def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                metadata={},
            )

    service = AgentSearchService(
        candidate_recommendation_service=CandidateService(),
        index_state_provider=lambda: {
            "index_complete": False,
            "index_backfill_failed": True,
        },
    )

    outcome = asyncio.run(
        service.recommend(
            AgentRecommendationCommand(
                question="writer",
                top_k=1,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=2,
                vector_min_score=0.01,
                rerank_min_score=None,
                min_score_explicit=False,
            )
        )
    )

    assert outcome.response.metadata["index_complete"] is False
    assert "index_incomplete" in outcome.response.metadata["reasons"]


def test_search_index_state_metadata_marks_failed_backfill() -> None:
    backfill = IndexBackfillStatus()
    backfill.mark_failed("EmbeddingAPIError")

    metadata = search_index_state_metadata(
        Registry(
            VectorStore(),
            backfill,
        )
    )

    assert metadata["index_complete"] is False
    assert metadata["index_backfill_failed"] is True
    assert metadata["index_backfill_error_type"] == "EmbeddingAPIError"
    assert metadata["index_size"] == 457
    assert metadata["index_pending_count"] == 43


def test_search_index_state_metadata_marks_probe_failure_explicitly() -> None:
    class BrokenRegistry:
        def get(self, name: str) -> Any:
            raise RuntimeError("registry unavailable")

    metadata = search_index_state_metadata(BrokenRegistry())

    assert metadata["index_complete"] is False
    assert metadata["index_state_unavailable"] is True


def test_incomplete_index_results_are_not_positive_cached() -> None:
    from app.application.agent_search_cache import AgentSearchResultCache

    cache = AgentSearchResultCache(
        index_version_provider=lambda: 1,
        max_entries=10,
        ttl_seconds=60,
        clock=lambda: 1.0,
    )
    service = AgentSearchService(
        embedding_generator=Embedding(),
        vector_match_service=Match(),
        result_cache=cache,
        index_state_provider=lambda: {"index_complete": False},
    )

    asyncio.run(service.search(AgentSearchCommand(query="writer", top_k=1)))

    assert cache.stats["entries"] == 0


def test_cache_hit_reclassifies_the_current_index_state() -> None:
    from app.application.agent_search_cache import AgentSearchResultCache

    cache = AgentSearchResultCache(
        index_version_provider=lambda: 1,
        max_entries=10,
        ttl_seconds=60,
        clock=lambda: 1.0,
    )
    index_state: dict[str, Any] = {"index_complete": True}
    service = AgentSearchService(
        embedding_generator=Embedding(),
        vector_match_service=Match(),
        result_cache=cache,
        index_state_provider=lambda: index_state,
    )

    first = asyncio.run(
        service.search(AgentSearchCommand(query="writer", top_k=1))
    )
    index_state["index_complete"] = False
    second = asyncio.run(
        service.search(AgentSearchCommand(query="writer", top_k=1))
    )

    assert first.metadata["status"] == "ok"
    assert second.metadata["cache_hit"] is True
    assert second.metadata["index_complete"] is False
    assert second.metadata["status"] == "degraded"
    assert "index_incomplete" in second.metadata["reasons"]
