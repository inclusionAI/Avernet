"""Normalize transport-independent recommendation candidate provenance."""

from __future__ import annotations

from typing import Any


def apply_recommendation_candidate_source(
    metadata: dict[str, Any],
) -> None:
    """Record the actual fallback source without leaking retrieval internals."""

    if (
        metadata.get("literal_fallback_used") is True
        or metadata.get("literal_fallback_default_used") is True
        or metadata.get("score_source") == "regex_fallback"
    ):
        metadata["candidate_source"] = "literal_fallback"
        return
    if metadata.get("keyword_search_used") is not True:
        return
    dense_degraded = (
        metadata.get("semantic_unavailable") is True
        or metadata.get("dense_unavailable") is True
    )
    metadata["candidate_source"] = (
        "keyword_fallback" if dense_degraded else "hybrid"
    )


__all__ = ["apply_recommendation_candidate_source"]
