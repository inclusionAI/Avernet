"""TDD: candidate recommendation must preserve real embedding admission failures."""

from __future__ import annotations

import httpx
from search_core.providers.embedding import EmbeddingAPIError

from app.application.services.worker_candidate_recommendation_impl import (
    WorkerCandidateRecommendationImpl,
)
from app.domain.models.retrieval_mode import RetrievalMode
from app.infra.config.feature_flags import FeatureFlags


class RateLimitedEmbedding:
    def embed(self, question: str) -> list[float]:
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


class EmptyKeywordMatch:
    def match(self, **kwargs):
        kwargs["runtime_config"]["_retrieval"] = {
            "semantic_unavailable": True,
            "keyword_search_used": True,
            "keyword_unavailable": False,
            "literal_fallback_used": False,
            "rerank_degraded": False,
            "score_source": "hybrid_rrf",
        }
        return []


def test_candidate_recommendation_preserves_embedding_429_for_empty_fallback(monkeypatch):
    monkeypatch.setattr(
        FeatureFlags,
        "is_vector_aware_recommendation_enabled",
        classmethod(lambda cls: True),
    )
    monkeypatch.setattr(
        FeatureFlags,
        "is_real_embedding_enabled",
        classmethod(lambda cls: True),
    )
    service = WorkerCandidateRecommendationImpl(
        retrieval_service=object(),
        vector_match_service=EmptyKeywordMatch(),
        embedding_generator=RateLimitedEmbedding(),
    )

    response = service.recommend(
        question="reader",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        participants=None,
        max_candidates=5,
        runtime_config={},
    )

    assert response.recommendations == []
    assert response.metadata["semantic_unavailable"] is True
    assert response.metadata["semantic_status_code"] == 429
    assert response.metadata["semantic_retry_after_seconds"] == 1
    assert response.metadata["semantic_reason_code"] == "embedding_quota_exhausted"
    assert response.metadata["semantic_provider_reason_code"] == "RPM_LIMIT_EXCEEDED"


def test_candidate_recommendation_marks_unexpected_internal_failure(monkeypatch):
    monkeypatch.setattr(
        FeatureFlags,
        "is_vector_aware_recommendation_enabled",
        classmethod(lambda cls: True),
    )
    service = WorkerCandidateRecommendationImpl(retrieval_service=object())

    def fail_sufficiency(**_kwargs):
        raise RuntimeError("profile store unavailable")

    monkeypatch.setattr(service, "_assess_sufficiency", fail_sufficiency)

    response = service.recommend(
        question="reader",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        participants=None,
        max_candidates=5,
        runtime_config={},
    )

    assert response.recommendations == []
    assert response.metadata["candidate_unavailable"] is True
    assert response.metadata["candidate_error"] == "RuntimeError"


def test_candidate_recommendation_embedding_429_falls_back_to_keyword_match(
    monkeypatch, tmp_path
):
    from search_core.plugin_api.vector_point import VectorPoint
    from search_core.providers.fallback import RegexFallbackIndex

    from app.application.services.worker_vector_match_service import (
        FragmentRetrievalConfig,
        WorkerVectorMatchService,
    )
    from app.infra.metadatastores.file_metadata_store_adapter import (
        FileMetadataStoreAdapter,
    )

    class HealthyButEmptyKeywordVectorStore:
        def size(self) -> int:
            return 1

        def search(self, *args, **kwargs):
            raise AssertionError("semantic search must be skipped after 429")

        def text_search(self, *args, **kwargs):
            return []

    point = VectorPoint(
        id="bot:reader:default",
        vector=[0.0],
        payload={
            "profile_key": "bot:reader:default",
            "worker_id": "bot:reader",
            "profile_id": "default",
            "content": "reader document search",
            "runtime_state": "online",
            "availability": "public",
        },
    )
    vector_match = WorkerVectorMatchService(
        vector_store=HealthyButEmptyKeywordVectorStore(),
        metadata_store=FileMetadataStoreAdapter(
            storage_dir=str(tmp_path / "metadata")
        ),
        fallback_index=RegexFallbackIndex([point]),
        fragment_config=FragmentRetrievalConfig(
            enable_fragment_embedding=True,
            reranker_model=None,
        ),
    )

    monkeypatch.setattr(
        FeatureFlags,
        "is_vector_aware_recommendation_enabled",
        classmethod(lambda cls: True),
    )
    monkeypatch.setattr(
        FeatureFlags,
        "is_real_embedding_enabled",
        classmethod(lambda cls: True),
    )

    runtime_config: dict = {}
    service = WorkerCandidateRecommendationImpl(
        retrieval_service=object(),
        vector_match_service=vector_match,
        embedding_generator=RateLimitedEmbedding(),
    )
    response = service.recommend(
        question="reader document search",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        participants=None,
        max_candidates=1,
        runtime_config=runtime_config,
        vector_min_score=0.01,
        rerank_min_score=0.01,
    )

    assert [item.profile_key for item in response.recommendations] == [
        "bot:reader:default"
    ]
    metadata = response.metadata
    assert metadata["semantic_unavailable"] is True
    assert metadata["semantic_status_code"] == 429
    assert metadata["semantic_retry_after_seconds"] == 1
    assert metadata["keyword_unavailable"] is False
    assert metadata["literal_fallback_used"] is True
    assert metadata["literal_fallback_default_used"] is False
    assert metadata["candidate_source"] == "literal_fallback"
