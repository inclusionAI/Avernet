from __future__ import annotations

from app.application.agent_search_cache import AgentSearchResultCache
from app.infra.public.config.drm_config_provider import DrmConfigProvider


def test_default_recommendation_threshold_preserves_recall(monkeypatch) -> None:
    """The implicit recall threshold must match the request schema default."""

    monkeypatch.delenv("BCSFUSE_RECOMMEND_MIN_SCORE", raising=False)

    provider = DrmConfigProvider()

    score = provider.get_recommend_min_score()

    assert score == 0.01


def _cache_for_key_contract() -> AgentSearchResultCache:
    return AgentSearchResultCache(
        index_version_provider=lambda: 7,
        max_entries=10,
        ttl_seconds=30.0,
    )


def _bind_default_drm(monkeypatch) -> None:
    from app.application.utils.drm_config_helper import set_drm_config

    for key in (
        "BCSFUSE_RECOMMEND_MIN_SCORE",
        "BCSFUSE_VECTOR_MIN_SCORE",
        "BCSFUSE_RERANK_MIN_SCORE",
    ):
        monkeypatch.delenv(key, raising=False)
    set_drm_config(DrmConfigProvider())


def test_recommendation_cache_key_uses_recall_default(monkeypatch) -> None:
    """Both rerank modes must cache with the implicit recall threshold."""

    from app.adapters.http.recommend_response_cache import (
        V1_RECOMMEND_RESPONSE_CACHE_OPERATION,
        build_v1_recommendation_response_cache_key,
    )
    from app.domain.models.bot_recommendation import BotRecommendationRequest
    from app.infra.config.feature_flags import FeatureFlags

    _bind_default_drm(monkeypatch)
    cache = _cache_for_key_contract()

    actual = build_v1_recommendation_response_cache_key(
        result_cache=cache,
        request=BotRecommendationRequest(
            question="Python search engine optimization",
            topK=3,
        ),
        index_version=7,
    )

    expected = cache.key(
        V1_RECOMMEND_RESPONSE_CACHE_OPERATION,
        index_version=7,
        material={
            "normalized_query": "python search engine optimization",
            "top_k": 3,
            "driver_bot_id": None,
            "type": "recommend",
            "explicit_min_score": False,
            "min_score": 0.01,
            "vector_min_score": 0.01,
            "rerank_min_score": 0.01,
            "expand_factor": 3,
            "enable_rerank": True,
            "reranker_model": "Qwen3-Reranker-4B",
            "filters": None,
            "profile_embedding_enabled": FeatureFlags.is_profile_embedding_index_enabled(),
        },
    )

    assert actual == expected


def test_recommendation_cache_key_without_rerank_uses_recall_default(
    monkeypatch,
) -> None:
    """The legacy unified path must not reintroduce the 0.6 cutoff in cache keys."""

    from app.adapters.http.recommend_response_cache import (
        V1_RECOMMEND_RESPONSE_CACHE_OPERATION,
        build_v1_recommendation_response_cache_key,
    )
    from app.domain.models.bot_recommendation import BotRecommendationRequest
    from app.infra.config.feature_flags import FeatureFlags

    _bind_default_drm(monkeypatch)
    cache = _cache_for_key_contract()

    actual = build_v1_recommendation_response_cache_key(
        result_cache=cache,
        request=BotRecommendationRequest(
            question="Python search engine optimization",
            topK=3,
            enable_rerank=False,
        ),
        index_version=7,
    )

    expected = cache.key(
        V1_RECOMMEND_RESPONSE_CACHE_OPERATION,
        index_version=7,
        material={
            "normalized_query": "python search engine optimization",
            "top_k": 3,
            "driver_bot_id": None,
            "type": "recommend",
            "explicit_min_score": False,
            "min_score": 0.01,
            "vector_min_score": 0.01,
            "rerank_min_score": 0.01,
            "expand_factor": 3,
            "enable_rerank": False,
            "reranker_model": None,
            "filters": None,
            "profile_embedding_enabled": FeatureFlags.is_profile_embedding_index_enabled(),
        },
    )

    assert actual == expected
