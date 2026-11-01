"""TDD: degraded fallback sources must not be projected as normal vectors."""

from __future__ import annotations

from types import SimpleNamespace

from app.adapters.http.search_response_projection import build_v1_search_response
from app.application.agent_search_service import AgentSearchOutcome
from app.bootstrap.oss_business_routes import SearchRequestOSS


def _outcome(metadata: dict) -> AgentSearchOutcome:
    result = SimpleNamespace(
        profile_key="bot:reader:default",
        score=0.42,
        aggregated_score=0.42,
        metadata=SimpleNamespace(
            short_profile="reader", payload={"short_profile": "reader"}
        ),
        reasons=[],
        fragment_matches=[],
    )
    return AgentSearchOutcome(
        query="reader",
        normalized_query="reader",
        top_k=1,
        mode="auto",
        results=(result,),
        metadata=metadata,
        config_applied={"reranker_model": None},
        elapsed_ms=10.0,
    )


def test_keyword_fallback_result_is_projected_with_explicit_source() -> None:
    response = build_v1_search_response(
        SearchRequestOSS(query="reader", top_k=1),
        _outcome(
            {
                "semantic_unavailable": True,
                "semantic_status_code": 429,
                "semantic_reason_code": "embedding_quota_exhausted",
                "keyword_search_used": True,
                "score_source": "hybrid_rrf",
            }
        ),
        elapsed_ms=12.0,
    )

    assert response["reasons"] == [
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "keyword_fallback_used",
    ]
    assert response["results"][0]["source"] == "keyword_fallback"


def test_dense_only_projection_keeps_vector_source() -> None:
    response = build_v1_search_response(
        SearchRequestOSS(query="reader", top_k=1),
        _outcome({"score_source": "vector_weighted"}),
        elapsed_ms=12.0,
    )

    assert response["status"] == "ok"
    assert response["results"][0]["source"] == "vector"


def test_provider_reason_code_is_projected_for_quota_exhaustion() -> None:
    response = build_v1_search_response(
        SearchRequestOSS(query="reader", top_k=1),
        _outcome(
            {
                "semantic_unavailable": True,
                "semantic_status_code": 429,
                "semantic_reason_code": "embedding_quota_exhausted",
                "semantic_provider_reason_code": "RPM_LIMIT_EXCEEDED",
                "keyword_search_used": True,
                "score_source": "hybrid_rrf",
            }
        ),
        elapsed_ms=12.0,
    )

    assert response["semantic_provider_reason_code"] == "RPM_LIMIT_EXCEEDED"
