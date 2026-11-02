"""TDD: bounded fallbacks remain explicit and the final fallback always returns."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from search_core.providers.embedding import EmbeddingAPIError

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
from app.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from app.domain.models.retrieval_mode import RetrievalMode
from app.application.services.worker_vector_match_types import MatchResult


class StartupProvider:
    async def initialize(self):
        return None

    async def shutdown(self):
        return None


class AcceptingAuthProvider:
    def validate_request(self, request):
        return True


class FakeVectorStore:
    def __init__(self):
        self.connection = SimpleNamespace(mode="server")
        self.collection_name = "worker_profiles"
        self.index_owner = "pod-a"
        self.index_version = 7
        self.index_lag_seconds = 0.0
        self.index_stale = False
        self.read_only = False
        self.index_state_unavailable = False

    def check_ready(self):
        return True


def _context() -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", FakeVectorStore())
    registry.register("auth", AcceptingAuthProvider())
    return ApplicationContext(
        mode="runtime", startup_profile="opensource", registry=registry
    )


class RateLimitedEmbedding:
    def embed(self, text: str) -> list[float]:
        raise EmbeddingAPIError("429 - request quota exceeded", status_code=429)


def _retrieval_marker(runtime_config: dict, **extra: object) -> dict:
    marker = {
        "score_source": "hybrid_rrf",
        "semantic_unavailable": True,
        "keyword_search_used": True,
        "keyword_unavailable": False,
        "literal_fallback_used": False,
        "rerank_degraded": False,
    }
    marker.update(extra)
    runtime_config["_retrieval"] = marker
    return marker


def _keyword_result(profile_key: str = "bot:reader:default") -> MatchResult:
    return MatchResult(
        profile_key=profile_key,
        metadata=SimpleNamespace(
            profile_key=profile_key, payload={}, short_profile="reader"
        ),
        score=1.0,
    )


def test_search_rate_limited_embedding_without_any_fallback_returns_final_envelope(monkeypatch):
    from app.adapters.http.dependencies import fusion_dependencies

    class NoFallbackMatch:
        def match(self, **kwargs):
            _retrieval_marker(kwargs["runtime_config"], semantic_status_code=429)
            return []

    monkeypatch.setattr(
        fusion_dependencies, "_get_embedding_generator", lambda: RateLimitedEmbedding()
    )
    monkeypatch.setattr(
        fusion_dependencies, "_get_vector_match_service", lambda: NoFallbackMatch()
    )
    app = create_bcsfuse_app(_context())

    with TestClient(app) as client:
        response = client.post("/api/v1/search", json={"query": "reader expert", "top_k": 5})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "fallback_unavailable"
    assert body["semantic_status_code"] == 429
    assert "fallback_unavailable" in body["reasons"]


def test_search_rate_limited_embedding_with_keyword_hits_keeps_degraded_200(
    monkeypatch,
):
    from app.adapters.http.dependencies import fusion_dependencies

    class KeywordMatch:
        def match(self, **kwargs):
            _retrieval_marker(kwargs["runtime_config"], semantic_status_code=429)
            return [_keyword_result()]

    monkeypatch.setattr(
        fusion_dependencies, "_get_embedding_generator", lambda: RateLimitedEmbedding()
    )
    monkeypatch.setattr(
        fusion_dependencies, "_get_vector_match_service", lambda: KeywordMatch()
    )
    app = create_bcsfuse_app(_context())

    with TestClient(app) as client:
        response = client.post("/api/v1/search", json={"query": "reader expert", "top_k": 5})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results_count"] == 1
    assert body["status"] == "degraded"
    assert body["reasons"] == [
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "keyword_fallback_used",
    ]


def test_search_rate_limited_embedding_with_keyword_empty_uses_literal_fallback(
    monkeypatch,
):
    """A healthy-but-empty keyword cluster must not block local failover."""

    from app.adapters.http.dependencies import fusion_dependencies

    class LiteralFallbackMatch:
        def match(self, **kwargs):
            _retrieval_marker(
                kwargs["runtime_config"],
                semantic_status_code=429,
                keyword_search_used=False,
                score_source="regex_fallback",
                literal_fallback_used=True,
                literal_fallback_default_used=True,
            )
            return [
                MatchResult(
                    profile_key="bot:reader:default",
                    metadata=SimpleNamespace(
                        profile_key="bot:reader:default",
                        payload={},
                        short_profile="reader",
                    ),
                    score=0.01,
                    reasons=["literal_fallback_default"],
                )
            ]

    monkeypatch.setattr(
        fusion_dependencies, "_get_embedding_generator", lambda: RateLimitedEmbedding()
    )
    monkeypatch.setattr(
        fusion_dependencies,
        "_get_vector_match_service",
        lambda: LiteralFallbackMatch(),
    )
    app = create_bcsfuse_app(_context())

    with TestClient(app) as client:
        response = client.post("/api/v1/search", json={"query": "unrelated query", "top_k": 5})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results_count"] == 1
    assert body["status"] == "degraded"
    assert body["reasons"] == [
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "literal_fallback_used",
        "literal_fallback_default",
        "regex_fallback",
    ]
    assert body["semantic_status_code"] == 429
    assert body["results"][0]["source"] == "literal_fallback_default"


def test_recommend_rate_limited_semantic_failure_without_hits_returns_final_envelope(monkeypatch):
    from app.adapters.http import recommend_routes
    from app.adapters.http.dependencies import fusion_dependencies as fusion_dependencies_module

    class EmptyCandidateService:
        _vector_match_service = None
        _embedding_generator = None

        def recommend(self, **kwargs):
            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                metadata={
                    "semantic_unavailable": True,
                    "semantic_status_code": 429,
                    "semantic_retry_after_seconds": 1,
                },
            )

    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        lambda: EmptyCandidateService(),
    )
    app = create_bcsfuse_app(_context())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": "reader",
                "topK": 5,
                "enable_rerank": False,
                "expand_factor": 3,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    metadata = body["metadata"]
    assert metadata["status"] == "fallback_unavailable"
    assert metadata["semantic_status_code"] == 429
    assert metadata["semantic_retry_after_seconds"] == 1.0
    assert "fallback_unavailable" in metadata["reasons"]


def test_recommend_rate_limited_semantic_failure_with_literal_fallback_keeps_degraded_200(
    monkeypatch,
):
    from app.adapters.http import recommend_routes

    class LiteralFallbackCandidateService:
        _vector_match_service = None
        _embedding_generator = None

        def recommend(self, **kwargs):
            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                recommendations=[
                    CandidateRecommendation(
                        profile_key="bot:reader:default",
                        worker_id="bot:reader",
                        score=0.01,
                    )
                ],
                total_candidates=1,
                selected_candidates=1,
                metadata={
                    "semantic_unavailable": True,
                    "semantic_status_code": 429,
                    "semantic_retry_after_seconds": 1,
                    "keyword_unavailable": False,
                    "keyword_search_used": False,
                    "score_source": "regex_fallback",
                    "literal_fallback_used": True,
                    "literal_fallback_default_used": True,
                },
            )

    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        lambda: LiteralFallbackCandidateService(),
    )
    app = create_bcsfuse_app(_context())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": "unrelated query",
                "topK": 5,
                "enable_rerank": False,
                "expand_factor": 3,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["recommendations"]) == 1
    assert body["metadata"]["status"] == "degraded"
    assert body["metadata"]["reasons"] == [
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "literal_fallback_used",
        "literal_fallback_default",
        "regex_fallback",
    ]
    assert body["metadata"]["semantic_status_code"] == 429
    assert body["metadata"]["vector_search_used"] is False
    assert body["metadata"]["keyword_search_used"] is False
    assert body["metadata"]["literal_fallback_default_used"] is True


def test_recommend_rate_limited_semantic_failure_with_keyword_hits_keeps_degraded_200(monkeypatch):
    from app.adapters.http import recommend_routes

    class KeywordCandidateService:
        _vector_match_service = None
        _embedding_generator = None

        def recommend(self, **kwargs):
            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                recommendations=[
                    CandidateRecommendation(
                        profile_key="bot:reader:default",
                        worker_id="bot:reader",
                        score=1.0,
                    )
                ],
                total_candidates=1,
                selected_candidates=1,
                metadata={
                    "semantic_unavailable": True,
                    "semantic_status_code": 429,
                    "semantic_retry_after_seconds": 1,
                    "keyword_search_used": True,
                },
            )

    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        lambda: KeywordCandidateService(),
    )
    app = create_bcsfuse_app(_context())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": "reader",
                "topK": 5,
                "enable_rerank": False,
                "expand_factor": 3,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["recommendations"]) == 1
    assert body["metadata"]["status"] == "degraded"
    assert body["metadata"]["reasons"] == [
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "keyword_fallback_used",
    ]


def test_recommend_unexpected_candidate_failure_returns_final_envelope(monkeypatch):
    from app.adapters.http import recommend_routes

    class UnexpectedFailureCandidateService:
        _vector_match_service = None
        _embedding_generator = None

        def recommend(self, **kwargs):
            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                metadata={"candidate_unavailable": True},
            )

    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        lambda: UnexpectedFailureCandidateService(),
    )
    app = create_bcsfuse_app(_context())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": "reader",
                "topK": 5,
                "enable_rerank": False,
                "expand_factor": 3,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    metadata = body["metadata"]
    assert metadata["status"] == "fallback_unavailable"
    assert metadata["fallback_reason_code"] == "CANDIDATE_RETRIEVAL_UNAVAILABLE"
