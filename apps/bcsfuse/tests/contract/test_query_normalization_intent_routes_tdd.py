"""TDD contracts for stable query keys and zero-embedding direct routes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.application.agent_search_cache import AgentSearchResultCache
from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchService,
    normalize_query,
)
from app.application.services.query_intent_policy import (
    QueryIntent,
    resolve_query_intent,
)
from app.application.services.query_normalizer import normalize_query_text
from app.domain.models.candidate_recommendation import CandidateRecommendationResponse
from app.domain.models.metadata_record import MetadataRecord
from app.domain.models.retrieval_mode import RetrievalMode
from app.application.services.worker_candidate_recommendation_impl import (
    WorkerCandidateRecommendationImpl,
)
from app.application.services.worker_vector_match_types import MatchResult


def test_normalizer_collapses_unicode_case_and_punctuation_equivalence() -> None:
    raw = "　Ｐｙｔｈｏｎ　BOT，  风险查询！"
    alternate = "  python bot, 风险查询!  "

    assert normalize_query_text(raw) == normalize_query_text(alternate)
    assert normalize_query_text(raw) == "python bot, 风险查询!"


def test_normalizer_preserves_identifier_separators() -> None:
    assert (
        normalize_query_text("　BOT:Reader:DEFAULT　")
        == "bot:reader:default"
    )


def test_core_route_and_response_cache_share_one_normalizer() -> None:
    raw = "　Ｐｙｔｈｏｎ　BOT，  风险查询！"
    alternate = "python bot, 风险查询!"

    cache = AgentSearchResultCache(
        index_version_provider=lambda: 7,
        max_entries=10,
        ttl_seconds=30,
    )

    assert normalize_query(raw) == normalize_query_text(raw)
    assert cache.normalize_query(raw) == normalize_query_text(raw)
    key_material = {
        "operation": "search",
        "index_version": 7,
        "material": {
            "normalized_query": cache.normalize_query(raw),
            "top_k": 3,
        },
    }
    key_a = cache.key("search", index_version=7, material=key_material["material"])
    key_b = cache.key(
        "search",
        index_version=7,
        material={
            "normalized_query": cache.normalize_query(alternate),
            "top_k": 3,
        },
    )
    assert key_a == key_b


@pytest.mark.parametrize(
    ("query", "expected", "reason"),
    [
        ("bot:reader:default", QueryIntent.DIRECT_KEYWORD, "identifier"),
        ("READER", QueryIntent.DIRECT_KEYWORD, "simple_identifier"),
        ("find agent reader", QueryIntent.DIRECT_KEYWORD, "explicit_lookup"),
        ("python search engine optimization", QueryIntent.SEMANTIC, "natural_language"),
        ("帮我找一个支付风险智能体", QueryIntent.SEMANTIC, "natural_language"),
    ],
)
def test_intent_policy_routes_bounded_queries_without_semantic_cost(
    query: str, expected: str, reason: str
) -> None:
    intent = resolve_query_intent(normalize_query_text(query))
    assert intent.route == expected
    assert intent.reason == reason
    if reason == "explicit_lookup":
        assert intent.target == "reader"
    else:
        assert intent.target == normalize_query_text(query)


@dataclass
class RecordingCore:
    embedding_calls: list[str] = field(default_factory=list)
    match_calls: list[dict[str, Any]] = field(default_factory=list)
    recommend_calls: list[dict[str, Any]] = field(default_factory=list)

    def embed(self, query: str) -> list[float]:
        self.embedding_calls.append(query)
        return [0.5]

    def match(self, **kwargs: Any) -> list[Any]:
        self.match_calls.append(kwargs)
        return [
            MatchResult(
                profile_key="bot:reader:default",
                metadata=MetadataRecord(
                    profile_key="bot:reader:default",
                    vector_id=None,
                    staff_id="bot:reader",
                    profile_id="default",
                    profile_type="default",
                    source_root="test",
                ),
                score=0.9,
            )
        ]

    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        self.recommend_calls.append(kwargs)
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            metadata={"candidate_source": "vector"},
        )


def test_direct_search_route_skips_embedding_before_retrieval() -> None:
    core = RecordingCore()
    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        candidate_recommendation_service=core,
    )

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="BOT:Reader:DEFAULT", top_k=3))
    )

    assert core.embedding_calls == []
    assert core.match_calls[0]["query_embedding"] == []
    assert core.match_calls[0]["query"] == "bot:reader:default"
    assert outcome.metadata["query_route"] == "direct_keyword"
    assert outcome.metadata["query_intent"] == "identifier"
    assert outcome.metadata.get("semantic_unavailable") is not True


def test_semantic_search_route_still_calls_embedding() -> None:
    core = RecordingCore()
    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        candidate_recommendation_service=core,
    )

    asyncio.run(
        service.search(
            AgentSearchCommand(query="python search engine optimization", top_k=3)
        )
    )

    assert core.embedding_calls == ["python search engine optimization"]
    assert core.match_calls[0]["runtime_config"]["query_route"] == "semantic"


class RecordingEmbedding:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.5]


class DirectVectorMatch:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def match(self, **kwargs: Any) -> list[MatchResult]:
        self.calls.append(kwargs)
        return [
            MatchResult(
                profile_key="bot:reader:default",
                metadata=MetadataRecord(
                    profile_key="bot:reader:default",
                    vector_id=None,
                    staff_id="bot:reader",
                    profile_id="default",
                    profile_type="default",
                    source_root="test",
                ),
                score=0.9,
            )
        ]


class EmptyRetrieval:
    pass


def test_direct_recommendation_route_skips_real_embedding(
    monkeypatch,
) -> None:
    from app.infra.config.feature_flags import FeatureFlags

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

    embedding = RecordingEmbedding()
    vector_match = DirectVectorMatch()
    candidate_service = WorkerCandidateRecommendationImpl(
        retrieval_service=EmptyRetrieval(),
        vector_match_service=vector_match,
        embedding_generator=embedding,
    )
    service = AgentSearchService(candidate_recommendation_service=candidate_service)

    outcome = asyncio.run(
        service.recommend(
            AgentRecommendationCommand(
                question="BOT:Reader:DEFAULT",
                top_k=1,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=1,
                vector_min_score=0.01,
                rerank_min_score=None,
                min_score_explicit=False,
            )
        )
    )

    assert embedding.calls == []
    assert vector_match.calls[0]["query_embedding"] == []
    assert vector_match.calls[0]["query"] == "bot:reader:default"
    assert len(outcome.response.recommendations) == 1
    assert outcome.response.metadata["query_route"] == "direct_keyword"
