"""Cache and resilience predicates shared by the unified search service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def has_degradation_markers(metadata: Mapping[str, Any]) -> bool:
    """Return whether an outcome is explicit degraded data and never cacheable."""

    if metadata.get("keyword_unavailable") is True:
        return True
    if metadata.get("semantic_unavailable") is True:
        return True
    if metadata.get("literal_fallback_used") is True:
        return True
    if metadata.get("score_source") == "regex_fallback":
        return True
    if metadata.get("retrieval_unavailable") is True:
        return True
    if metadata.get("candidate_unavailable") is True:
        return True
    if metadata.get("local_snapshot_used") is True:
        return True
    if metadata.get("last_known_good_used") is True:
        return True
    if metadata.get("index_complete") is False:
        return True
    return metadata.get("fallback_unavailable") is True


def outcome_is_empty(outcome: Any) -> bool:
    """Detect a stale-cacheable empty response without transport coupling."""

    response = getattr(outcome, "response", None)
    if response is not None:
        return not getattr(response, "recommendations", None)
    return not getattr(outcome, "results", None)


def ensure_search_candidate_source(metadata: dict[str, Any]) -> None:
    """Make every non-empty retrieval score traceable to a candidate source."""

    if metadata.get("candidate_source"):
        return
    score_source = metadata.get("score_source")
    keyword_used = metadata.get("keyword_search_used") is True
    dense_degraded = (
        metadata.get("semantic_unavailable") is True
        or metadata.get("dense_unavailable") is True
    )
    if score_source == "hybrid_rrf":
        metadata["candidate_source"] = (
            "keyword_fallback" if dense_degraded and keyword_used else "hybrid"
        )
    elif score_source == "regex_fallback":
        metadata["candidate_source"] = "literal_fallback"
    elif score_source == "direct_keyword":
        metadata["candidate_source"] = "direct_snapshot"
    elif score_source in {"vector_weighted", "reranker", "aggregate_fallback"}:
        metadata["candidate_source"] = "hybrid" if keyword_used else "dense"


def terminal_fallback(metadata: Mapping[str, Any]) -> bool:
    return metadata.get("fallback_unavailable") is True


def preserve_dependency_state(
    source: Mapping[str, Any], destination: dict[str, Any]
) -> None:
    """Carry dependency state into a previously known-good response."""

    for key in (
        "semantic_unavailable",
        "semantic_status_code",
        "semantic_reason_code",
        "semantic_retry_after_seconds",
        "keyword_unavailable",
        "keyword_search_used",
        "retrieval_unavailable",
        "retrieval_error",
        "candidate_unavailable",
        "candidate_error",
    ):
        if key in source:
            destination[key] = source[key]
    destination.pop("fallback_unavailable", None)
    destination.pop("fallback_reason_code", None)


__all__ = [
    "ensure_search_candidate_source",
    "has_degradation_markers",
    "outcome_is_empty",
    "preserve_dependency_state",
    "terminal_fallback",
]
