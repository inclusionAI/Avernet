"""Stable reason contract for the fast local semantic-outage path."""

from app.application.services.search_degradation_reasons import degraded_reasons


def test_keyword_bypass_reason_is_explicit() -> None:
    assert degraded_reasons({"keyword_bypassed": True}) == ("keyword_bypassed",)
    assert degraded_reasons(
        {
            "semantic_unavailable": True,
            "semantic_reason_code": "EMBEDDING_QUOTA_EXHAUSTED",
            "keyword_unavailable": True,
            "literal_fallback_used": True,
        }
    ) == (
        "semantic_unavailable",
        "EMBEDDING_QUOTA_EXHAUSTED",
        "keyword_unavailable",
        "literal_fallback_used",
    )
