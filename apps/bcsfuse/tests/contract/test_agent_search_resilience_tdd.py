"""TDD contract for quick, bounded degradation without hidden empty results."""

from __future__ import annotations

import asyncio
from typing import Any

from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchService,
)
from app.application.agent_search_resilience_cache import (
    AgentSearchResilienceCache,
)
from app.application.services.agent_search_execution_budget import (
    AgentSearchExecutionBudget,
)


class RateLimitedEmbedding:
    def embed(self, text: str) -> list[float]:
        raise RuntimeError("429 embedding quota")


class FailingMatch:
    def __init__(self) -> None:
        self.calls = 0

    def match(self, **kwargs: Any) -> list[Any]:
        self.calls += 1
        raise RuntimeError("qdrant unavailable")


class SuccessfulMatch:
    def __init__(self) -> None:
        self.calls = 0

    def match(self, **kwargs: Any) -> list[Any]:
        self.calls += 1
        return [
            type(
                "Result",
                (),
                {
                    "profile_key": "worker:default",
                    "metadata": type("Meta", (), {"short_profile": "worker"})(),
                    "reasons": [],
                    "fragment_matches": [],
                    "score": 0.9,
                    "aggregated_score": 0.9,
                },
            )()
        ]


class FailingRecommendation:
    def __init__(self) -> None:
        self.calls = 0

    def recommend(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise RuntimeError("core recommendation failure")


class HealthyEmbedding:
    def embed(self, text: str) -> list[float]:
        return [1.0]


class GoodRecommendation:
    def __init__(self) -> None:
        self.calls = 0

    def recommend(self, **kwargs: Any) -> Any:
        from app.domain.models.candidate_recommendation import (
            CandidateRecommendation,
            CandidateRecommendationResponse,
        )

        self.calls += 1
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            metadata={},
            recommendations=[
                CandidateRecommendation(
                    profile_key="worker:default",
                    worker_id="worker",
                    score=0.9,
                )
            ],
        )


def snapshot() -> Any:
    from search_core.plugin_api.vector_point import VectorPoint
    from app.application.services.literal_fallback_maintenance import (
        LiteralFallbackSnapshot,
    )

    local = LiteralFallbackSnapshot()
    local.upsert(
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
                    "short_profile": "Python specialist",
                    "content": "Python vector retrieval and search",
                    "domains": ["backend"],
                    "active_skill_names": ["python"],
                    "runtime_state": "online",
                    "availability": "public",
                },
            )
        ]
    )
    return local


def resilience(clock: list[float] | None = None) -> AgentSearchResilienceCache:
    return AgentSearchResilienceCache(
        index_version_provider=lambda: 7,
        max_entries=100,
        last_known_good_ttl_seconds=600.0,
        negative_ttl_seconds=10.0,
        clock=(lambda: clock[-1]) if clock is not None else None,
    )


def budget() -> AgentSearchExecutionBudget:
    return AgentSearchExecutionBudget(
        embedding_timeout_seconds=0.05,
        retrieval_timeout_seconds=0.05,
        local_snapshot_timeout_seconds=0.05,
        total_timeout_seconds=0.15,
    )


def _search_command() -> AgentSearchCommand:
    return AgentSearchCommand(query="python search", top_k=3)


def _recommend_command() -> AgentRecommendationCommand:
    return AgentRecommendationCommand(
        question="python search",
        top_k=3,
        enable_rerank=False,
        reranker_model=None,
        expand_factor=2,
        vector_min_score=0.01,
        rerank_min_score=None,
        min_score_explicit=False,
    )


def test_search_uses_local_snapshot_before_terminal_empty_fallback() -> None:
    failing_match = FailingMatch()
    service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=failing_match,
        local_snapshot=snapshot(),
        execution_budget=budget(),
        resilience_cache=None,
    )

    outcome = asyncio.run(service.search(_search_command()))

    assert outcome.results
    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata.get("fallback_unavailable") is not True
    assert outcome.metadata["status"] == "degraded"
    assert "local_snapshot_used" in outcome.metadata["reasons"]


def test_local_snapshot_fallback_publishes_explicit_source_metadata() -> None:
    """Local-snapshot results must identify both candidate and score sources."""

    search_outcome = AgentSearchService.final_search_outcome(
        _search_command(),
        error_type="RuntimeError",
        local_snapshot=snapshot(),
    )
    recommend_outcome = AgentSearchService.final_recommendation_outcome(
        _recommend_command(),
        error_type="RuntimeError",
        local_snapshot=snapshot(),
    )

    assert search_outcome.results
    assert search_outcome.metadata["candidate_source"] == "local_snapshot"
    assert search_outcome.metadata["score_source"] == "local_snapshot"
    assert search_outcome.config_applied["_retrieval"]["candidate_source"] == "local_snapshot"
    assert search_outcome.config_applied["_retrieval"]["score_source"] == "local_snapshot"
    assert recommend_outcome.response.recommendations
    assert recommend_outcome.response.metadata["candidate_source"] == "local_snapshot"
    assert recommend_outcome.response.metadata["score_source"] == "local_snapshot"

    search_service = AgentSearchService(
        embedding_generator=HealthyEmbedding(),
        vector_match_service=FailingMatch(),
        local_snapshot=snapshot(),
        execution_budget=budget(),
        resilience_cache=None,
    )
    degraded_search = asyncio.run(search_service.search(_search_command()))
    retrieval = degraded_search.config_applied["_retrieval"]
    assert degraded_search.metadata["candidate_source"] == "local_snapshot"
    assert degraded_search.metadata["score_source"] == "local_snapshot"
    assert retrieval["candidate_source"] == "local_snapshot"
    assert retrieval["score_source"] == "local_snapshot"

    recommend_service = AgentSearchService(
        candidate_recommendation_service=FailingRecommendation(),
        local_snapshot=snapshot(),
        execution_budget=budget(),
        resilience_cache=None,
    )
    degraded_recommend = asyncio.run(recommend_service.recommend(_recommend_command()))
    assert degraded_recommend.response.metadata["candidate_source"] == "local_snapshot"
    assert degraded_recommend.response.metadata["score_source"] == "local_snapshot"


def test_recommendation_uses_local_snapshot_before_terminal_empty_fallback() -> None:
    service = AgentSearchService(
        candidate_recommendation_service=FailingRecommendation(),
        local_snapshot=snapshot(),
        execution_budget=budget(),
        resilience_cache=None,
    )

    outcome = asyncio.run(service.recommend(_recommend_command()))

    assert outcome.response.recommendations
    assert outcome.response.recommendations[0].profile_key == "worker:default"
    assert outcome.response.metadata["local_snapshot_used"] is True
    assert outcome.response.metadata["candidate_source"] == "local_snapshot"
    assert outcome.response.metadata["status"] == "degraded"
    assert "local_snapshot_used" in outcome.response.metadata["reasons"]


def test_last_known_good_supplies_a_stale_result_after_core_failure() -> None:
    clock = [0.0]
    cache = resilience(clock)
    good_match = SuccessfulMatch()
    service = AgentSearchService(
        embedding_generator=HealthyEmbedding(),
        vector_match_service=good_match,
        resilience_cache=cache,
    )

    success = asyncio.run(service.search(_search_command()))
    assert success.metadata.get("last_known_good_used") is not True

    clock.append(60.0)
    service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=FailingMatch(),
        resilience_cache=cache,
    )
    stale = asyncio.run(service.search(_search_command()))

    assert stale.results[0].profile_key == "worker:default"
    assert stale.metadata["last_known_good_used"] is True
    assert stale.metadata["last_known_good_age_seconds"] == 60.0
    assert stale.metadata["status"] == "degraded"
    assert "last_known_good_used" in stale.metadata["reasons"]


def test_terminal_empty_fallback_is_never_negative_cached() -> None:
    cache = resilience()
    failing_match = FailingMatch()
    service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=failing_match,
        resilience_cache=cache,
    )

    first = asyncio.run(service.search(_search_command()))
    second = asyncio.run(service.search(_search_command()))

    assert first.metadata["negative_cache_hit"] is False
    assert second.metadata["negative_cache_hit"] is False
    assert first.metadata["fallback_unavailable"] is True
    assert second.metadata["fallback_unavailable"] is True
    assert cache.negative_entries == 0
    assert failing_match.calls == 2

def test_index_version_changes_do_not_persist_empty_negatives() -> None:
    index_version = [7]
    cache = resilience()
    cache._index_version_provider = lambda: index_version[0]
    service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=FailingMatch(),
        resilience_cache=cache,
    )

    asyncio.run(service.search(_search_command()))
    assert cache.negative_entries == 0

    index_version[0] = 8
    outcome = asyncio.run(service.search(_search_command()))

    assert outcome.metadata.get("negative_cache_hit") is not True
    assert cache.negative_entries == 0


def test_total_timeout_uses_local_snapshot_instead_of_empty_final() -> None:
    class SleepingMatch:
        def match(self, **kwargs: Any) -> list[Any]:
            import time

            time.sleep(0.1)
            return []

    service = AgentSearchService(
        embedding_generator=HealthyEmbedding(),
        vector_match_service=SleepingMatch(),
        local_snapshot=snapshot(),
        resilience_cache=None,
        execution_budget=AgentSearchExecutionBudget(
            embedding_timeout_seconds=0.05,
            retrieval_timeout_seconds=0.05,
            local_snapshot_timeout_seconds=0.05,
            total_timeout_seconds=0.01,
        ),
    )

    outcome = asyncio.run(service.search(_search_command()))

    assert outcome.results
    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["retrieval_error"] == "TotalTimeout"
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata.get("fallback_unavailable") is not True
    assert outcome.metadata["status"] == "degraded"


def test_stage_timeout_fails_over_without_waiting_for_provider() -> None:
    class SleepingMatch:
        def __init__(self) -> None:
            self.calls = 0

        def match(self, **kwargs: Any) -> list[Any]:
            self.calls += 1
            import time

            time.sleep(0.1)
            return []

    service = AgentSearchService(
        embedding_generator=lambda text: [],
        vector_match_service=SleepingMatch(),
        local_snapshot=snapshot(),
        resilience_cache=None,
        execution_budget=AgentSearchExecutionBudget(
            embedding_timeout_seconds=0.05,
            retrieval_timeout_seconds=0.01,
            local_snapshot_timeout_seconds=0.05,
            total_timeout_seconds=0.15,
        ),
    )

    outcome = asyncio.run(service.search(_search_command()))

    assert outcome.results
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata.get("fallback_unavailable") is not True
