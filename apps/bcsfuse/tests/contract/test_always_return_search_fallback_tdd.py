"""TDD: admitted search/recommend commands must always return an explicit envelope."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
import httpx
from search_core.providers.embedding import EmbeddingAPIError, EmbeddingDimensionContractError

from app.adapters.http.search_response_projection import build_v1_search_response
from app.application.agent_search_cache import AgentSearchResultCache
from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchService,
    degraded_reasons,
)
from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.oss_business_routes import SearchRequestOSS
from app.bootstrap.provider_registry import ProviderRegistry
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
                    "short_profile": "Reserved search fallback worker",
                    "content": "search recommendation availability fallback",
                    "domains": ["backend"],
                    "active_skill_names": ["search"],
                    "runtime_state": "online",
                    "availability": "public",
                },
            )
        ]
    )
    return snapshot


class RateLimitedEmbedding:
    def embed(self, text: str) -> list[float]:
        raise EmbeddingAPIError(
            "429 - request quota exceeded",
            status_code=429,
            response=httpx.Response(
                429,
                headers={"Retry-After": "1"},
                json={
                    "error": {
                        "type": "RateLimitError",
                        "param": '{"errorCode":"RPM_LIMIT_EXCEEDED"}',
                        "code": "429",
                    }
                },
            ),
        )


class HealthyEmbedding:
    def embed(self, text: str) -> list[float]:
        return [1.0]



class DimensionMismatchEmbedding:
    def embed(self, text: str) -> list[float]:
        raise EmbeddingDimensionContractError(
            "configured dimension does not match model response", status_code=415
        )


class EmptyFallbackMatch:
    def match(self, **kwargs: Any) -> list[Any]:
        return []


class FailingMatch:
    def match(self, **kwargs: Any) -> list[Any]:
        raise RuntimeError("private qdrant endpoint detail")


class EmptyRecommendationService:
    _vector_match_service = None
    _embedding_generator = None

    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            metadata={
                "semantic_unavailable": True,
                "semantic_status_code": 429,
                "semantic_retry_after_seconds": 1,
            },
        )


class FailingRecommendationService:
    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        raise RuntimeError("private profile store detail")


def _search_command(query: str = "reader", top_k: int = 5) -> AgentSearchCommand:
    return AgentSearchCommand(query=query, top_k=top_k)


def _recommend_command(question: str = "reader") -> AgentRecommendationCommand:
    return AgentRecommendationCommand(
        question=question,
        top_k=5,
        enable_rerank=False,
        reranker_model=None,
        expand_factor=2,
        vector_min_score=0.01,
        rerank_min_score=None,
        min_score_explicit=False,
    )


def test_search_embedding_dimension_contract_failure_uses_local_fallback() -> None:
    service = AgentSearchService(
        embedding_generator=DimensionMismatchEmbedding(),
        vector_match_service=EmptyFallbackMatch(),
        local_snapshot=_snapshot(),
    )

    outcome = asyncio.run(service.search(_search_command()))

    assert outcome.results
    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["semantic_unavailable"] is True
    assert outcome.metadata["semantic_status_code"] == 415
    assert outcome.metadata["semantic_reason_code"] == "embedding_dimension_contract_mismatch"
    assert outcome.metadata["local_snapshot_used"] is True
    assert degraded_reasons(outcome.metadata) == (
        "semantic_unavailable",
        "embedding_dimension_contract_mismatch",
        "local_snapshot_used",
    )


def test_search_semantic_outage_uses_non_empty_local_fallback() -> None:
    service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=EmptyFallbackMatch(),
        local_snapshot=_snapshot(),
    )

    outcome = asyncio.run(service.search(_search_command()))

    assert outcome.results
    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["semantic_unavailable"] is True
    assert outcome.metadata["semantic_status_code"] == 429
    assert outcome.metadata["semantic_provider_reason_code"] == "RPM_LIMIT_EXCEEDED"
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata.get("fallback_unavailable") is not True
    assert degraded_reasons(outcome.metadata) == (
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "local_snapshot_used",
    )
    response = build_v1_search_response(
        SearchRequestOSS(query="reader", top_k=5),
        outcome,
        elapsed_ms=1.0,
    )
    assert response["status"] == "degraded"
    assert response["results_count"] == 1
    assert response["results"][0]["source"] == "local_snapshot"
    assert response["semantic_status_code"] == 429
    assert response["semantic_retry_after_seconds"] == 1
    assert response["semantic_provider_reason_code"] == "RPM_LIMIT_EXCEEDED"


def test_search_unexpected_retrieval_failure_uses_non_empty_local_fallback() -> None:
    service = AgentSearchService(
        embedding_generator=HealthyEmbedding(),
        vector_match_service=FailingMatch(),
        local_snapshot=_snapshot(),
    )

    outcome = asyncio.run(service.search(_search_command("支付")))

    assert outcome.results
    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["retrieval_unavailable"] is True
    assert outcome.metadata["retrieval_error"] == "RuntimeError"
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata.get("fallback_unavailable") is not True
    assert "private qdrant endpoint detail" not in str(outcome.metadata)
    assert degraded_reasons(outcome.metadata) == (
        "retrieval_unavailable",
        "local_snapshot_used",
    )


def test_recommend_semantic_outage_uses_non_empty_local_fallback() -> None:
    service = AgentSearchService(
        candidate_recommendation_service=EmptyRecommendationService(),
        local_snapshot=_snapshot(),
    )

    outcome = asyncio.run(service.recommend(_recommend_command()))

    assert outcome.response.recommendations
    first = outcome.response.recommendations[0]
    assert first.profile_key == "worker:default"
    assert outcome.response.metadata["semantic_unavailable"] is True
    assert outcome.response.metadata["semantic_status_code"] == 429
    assert outcome.response.metadata["local_snapshot_used"] is True
    assert outcome.response.metadata["candidate_source"] == "local_snapshot"
    assert outcome.response.metadata["status"] == "degraded"
    assert outcome.response.metadata["reasons"] == [
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "local_snapshot_used",
    ]


def test_recommend_unexpected_core_failure_uses_non_empty_local_fallback() -> None:
    service = AgentSearchService(
        candidate_recommendation_service=FailingRecommendationService(),
        local_snapshot=_snapshot(),
    )

    outcome = asyncio.run(service.recommend(_recommend_command("支付")))

    assert outcome.response.recommendations
    assert outcome.response.recommendations[0].profile_key == "worker:default"
    assert outcome.response.metadata["candidate_unavailable"] is True
    assert outcome.response.metadata["candidate_error"] == "RuntimeError"
    assert outcome.response.metadata["local_snapshot_used"] is True
    assert outcome.response.metadata["candidate_source"] == "local_snapshot"
    assert "private profile store detail" not in str(outcome.response.metadata)
    assert outcome.response.metadata["status"] == "degraded"
    assert outcome.response.metadata["reasons"] == [
        "candidate_unavailable",
        "local_snapshot_used",
    ]


def test_final_fallback_is_never_cached() -> None:
    cache = AgentSearchResultCache(
        index_version_provider=lambda: 1,
        max_entries=10,
        ttl_seconds=60,
        clock=lambda: 1.0,
    )
    search_service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=EmptyFallbackMatch(),
        result_cache=cache,
    )
    recommend_service = AgentSearchService(
        candidate_recommendation_service=EmptyRecommendationService(),
        result_cache=cache,
    )

    search_outcome = asyncio.run(search_service.search(_search_command()))
    recommend_outcome = asyncio.run(recommend_service.recommend(_recommend_command()))

    assert search_outcome.metadata["fallback_unavailable"] is True
    assert recommend_outcome.response.metadata["fallback_unavailable"] is True
    assert cache.stats["entries"] == 0


def test_terminal_empty_fallback_is_not_negative_cached() -> None:
    from app.application.agent_search_resilience_cache import (
        AgentSearchResilienceCache,
    )

    resilience_cache = AgentSearchResilienceCache(
        index_version_provider=lambda: 1,
        max_entries=10,
        last_known_good_ttl_seconds=60.0,
        negative_ttl_seconds=10.0,
        clock=lambda: 1.0,
    )
    failing_match = FailingMatch()
    service = AgentSearchService(
        embedding_generator=HealthyEmbedding(),
        vector_match_service=failing_match,
        resilience_cache=resilience_cache,
    )

    asyncio.run(service.search(_search_command()))

    assert resilience_cache.negative_entries == 0


class _StartupProvider:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class _AcceptingAuth:
    def validate_request(self, request: Any) -> bool:
        return True


class _ReadyVectorStore:
    connection = type("Connection", (), {"mode": "server"})()
    collection_name = "worker_profiles"
    literal_fallback_index = _snapshot()
    index_owner = "pod-a"
    index_version = 1
    index_lag_seconds = 0.0
    index_stale = False
    read_only = False

    def check_ready(self) -> bool:
        return True


def _route_context() -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", _StartupProvider())
    registry.register("vector_store", _ReadyVectorStore())
    registry.register("auth", _AcceptingAuth())
    return ApplicationContext(
        mode="runtime", startup_profile="opensource", registry=registry
    )


def test_search_http_admitted_semantic_outage_still_returns_stable_200(monkeypatch) -> None:
    from app.adapters.http.dependencies import fusion_dependencies

    monkeypatch.setattr(
        fusion_dependencies,
        "_get_embedding_generator",
        lambda: RateLimitedEmbedding(),
    )
    monkeypatch.setattr(
        fusion_dependencies,
        "_get_vector_match_service",
        lambda: EmptyFallbackMatch(),
    )
    app = create_bcsfuse_app(_route_context())

    with TestClient(app) as client:
        response = client.post("/api/v1/search", json={"query": "reader", "top_k": 5})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert body["reasons"] == [
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "local_snapshot_used",
    ]
    assert len(body["results"]) == 1
    assert body["results"][0]["profile_key"] == "worker:default"


def test_recommend_http_admitted_semantic_outage_still_returns_stable_200(monkeypatch) -> None:
    from app.adapters.http import recommend_routes

    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        lambda: EmptyRecommendationService(),
    )
    app = create_bcsfuse_app(_route_context())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": "reader",
                "topK": 5,
                "enable_rerank": False,
                "expand_factor": 2,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metadata"]["status"] == "degraded"
    assert body["metadata"]["reasons"] == [
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "local_snapshot_used",
    ]
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["profile_key"] == "worker:default"


def test_recommend_http_provider_lookup_failure_returns_final_envelope(monkeypatch) -> None:
    from app.adapters.http import recommend_routes

    def unavailable_service():
        raise RuntimeError("private composition provider detail")

    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        unavailable_service,
    )
    app = create_bcsfuse_app(_route_context())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": "reader",
                "topK": 5,
                "enable_rerank": False,
                "expand_factor": 2,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metadata"]["status"] == "degraded"
    assert body["metadata"]["reasons"] == [
        "candidate_unavailable",
        "local_snapshot_used",
    ]
    assert "private composition provider detail" not in response.text
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["profile_key"] == "worker:default"


def test_admitted_fallback_envelopes_are_measured() -> None:
    from prometheus_client import REGISTRY

    def count(operation: str, reason_code: str) -> float:
        sample = REGISTRY.get_sample_value(
            "bcsfuse_search_fallback_unavailable_total",
            {"operation": operation, "reason_code": reason_code},
        )
        return 0.0 if sample is None else float(sample)

    search_service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=EmptyFallbackMatch(),
    )
    recommendation_service = AgentSearchService(
        candidate_recommendation_service=EmptyRecommendationService(),
    )

    asyncio.run(search_service.search(_search_command()))
    asyncio.run(recommendation_service.recommend(_recommend_command()))

    assert count(
        "search", "SEMANTIC_RETRIEVAL_UNAVAILABLE"
    ) >= 1
    assert count(
        "recommend", "SEMANTIC_RETRIEVAL_UNAVAILABLE"
    ) >= 1


def test_recommend_projection_failure_returns_final_envelope(monkeypatch) -> None:
    from app.adapters.http import recommend_routes

    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        lambda: EmptyRecommendationService(),
    )

    def fail_projection(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("private projection detail")

    monkeypatch.setattr(
        recommend_routes,
        "create_bot_recommendation_response",
        fail_projection,
    )
    app = create_bcsfuse_app(_route_context())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": "reader",
                "topK": 5,
                "enable_rerank": False,
                "expand_factor": 2,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metadata"]["status"] == "degraded"
    assert "fallback_reason_code" not in body["metadata"]
    assert body["metadata"]["candidate_error"] == "RuntimeError"
    assert body["metadata"]["local_snapshot_used"] is True
    assert "private projection detail" not in response.text
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["profile_key"] == "worker:default"


def test_search_projection_failure_returns_final_not_500(monkeypatch) -> None:
    from app.adapters.http import search_response_projection
    from app.adapters.http.dependencies import fusion_dependencies

    monkeypatch.setattr(
        fusion_dependencies,
        "_get_embedding_generator",
        lambda: HealthyEmbedding(),
    )
    monkeypatch.setattr(
        fusion_dependencies,
        "_get_vector_match_service",
        lambda: EmptyFallbackMatch(),
    )

    def fail_projection(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("private projection detail")

    monkeypatch.setattr(
        search_response_projection,
        "build_v1_search_response",
        fail_projection,
    )
    app = create_bcsfuse_app(_route_context())

    with TestClient(app) as client:
        response = client.post("/api/v1/search", json={"query": "reader", "top_k": 5})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "degraded"
    assert "fallback_reason_code" not in body
    assert body["retrieval_error"] == "RuntimeError"
    assert "private projection detail" not in response.text
    assert len(body["results"]) == 1
    assert body["results"][0]["profile_key"] == "worker:default"


def test_snapshot_recovered_fallback_is_not_reported_as_unavailable() -> None:
    from prometheus_client import REGISTRY

    def unavailable_count() -> tuple[float, float]:
        def value(name: str, reason: str, operation: str) -> float:
            sample = REGISTRY.get_sample_value(
                name,
                {"operation": operation, "reason_code": reason},
            )
            return 0.0 if sample is None else float(sample)

        return (
            value(
                "bcsfuse_search_fallback_unavailable_total",
                "SEARCH_RETRIEVAL_UNAVAILABLE",
                "search",
            ),
            value(
                "bcsfuse_search_fallback_unavailable_total",
                "CANDIDATE_RETRIEVAL_UNAVAILABLE",
                "recommend",
            ),
        )

    before = unavailable_count()
    search_outcome = AgentSearchService.final_search_outcome(
        _search_command(),
        error_type="RuntimeError",
        local_snapshot=_snapshot(),
    )
    recommend_outcome = AgentSearchService.final_recommendation_outcome(
        _recommend_command(),
        error_type="RuntimeError",
        local_snapshot=_snapshot(),
    )
    after = unavailable_count()

    assert search_outcome.results
    assert recommend_outcome.response.recommendations
    assert search_outcome.metadata["status"] == "degraded"
    assert recommend_outcome.response.metadata["status"] == "degraded"
    assert after == before


class NoMatchRecommendationService:
    """A healthy recommendation call that simply finds zero candidates."""

    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            metadata={},
        )


def test_search_empty_success_still_returns_local_snapshot() -> None:
    """A zero-hit semantic retrieval must not become an empty success."""

    service = AgentSearchService(
        embedding_generator=HealthyEmbedding(),
        vector_match_service=EmptyFallbackMatch(),
        local_snapshot=_snapshot(),
    )

    outcome = asyncio.run(service.search(_search_command("black hole")))

    assert outcome.results
    assert outcome.results[0].profile_key == "worker:default"
    assert outcome.metadata["local_snapshot_used"] is True
    assert outcome.metadata["status"] == "degraded"
    assert "local_snapshot_used" in outcome.metadata["reasons"]
    assert outcome.metadata.get("fallback_unavailable") is not True


def test_recommend_empty_success_still_returns_local_snapshot() -> None:
    """A zero-candidate recommendation result uses the public snapshot buffer."""

    service = AgentSearchService(
        candidate_recommendation_service=NoMatchRecommendationService(),
        local_snapshot=_snapshot(),
    )

    outcome = asyncio.run(service.recommend(_recommend_command("black hole")))

    assert outcome.response.recommendations
    assert outcome.response.recommendations[0].profile_key == "worker:default"
    assert outcome.response.metadata["local_snapshot_used"] is True
    assert outcome.response.metadata["candidate_source"] == "local_snapshot"
    assert outcome.response.metadata["status"] == "degraded"
    assert "local_snapshot_used" in outcome.response.metadata["reasons"]
    assert outcome.response.metadata.get("fallback_unavailable") is not True
