"""Shared fallback markers for search and recommendation outcomes."""

from __future__ import annotations

from typing import Any

from app.application.services.semantic_failure_metadata import (
    annotate_semantic_failure,
)
from app.infra.public.observability.search_dependency_metrics import (
    record_search_fallback_unavailable,
)


def mark_semantic_fallback(
    metadata: dict[str, Any], operation: str
) -> None:
    """Mark the terminal bounded-fallback state without inventing candidates."""

    annotate_semantic_failure(metadata)
    if metadata.get("semantic_unavailable") is not True:
        return
    if metadata.get("fallback_unavailable") is True:
        return
    metadata["fallback_unavailable"] = True
    metadata["fallback_reason_code"] = "SEMANTIC_RETRIEVAL_UNAVAILABLE"
    record_search_fallback_unavailable(
        operation, metadata["fallback_reason_code"]
    )


def mark_retrieval_unavailable(
    metadata: dict[str, Any], error: BaseException
) -> None:
    """Record a retrieval dependency failure before bounded recovery."""

    metadata["retrieval_unavailable"] = True
    metadata["retrieval_error"] = type(error).__name__


def mark_terminal_search_fallback(metadata: dict[str, Any]) -> None:
    """Turn a terminal search failure into a stable, non-success envelope."""

    if metadata.get("fallback_unavailable") is True:
        return
    metadata["fallback_unavailable"] = True
    metadata["fallback_reason_code"] = "SEARCH_RETRIEVAL_UNAVAILABLE"
    record_search_fallback_unavailable(
        "search", metadata["fallback_reason_code"]
    )


def mark_candidate_unavailable(
    metadata: dict[str, Any], error_type: str
) -> None:
    """Record a recommendation dependency failure before bounded recovery."""

    metadata["candidate_unavailable"] = True
    metadata["candidate_error"] = error_type


def mark_terminal_recommendation_fallback(
    metadata: dict[str, Any],
) -> None:
    """Turn a terminal recommendation failure into a stable envelope."""

    if metadata.get("fallback_unavailable") is True:
        return
    metadata["fallback_unavailable"] = True
    metadata["fallback_reason_code"] = "CANDIDATE_RETRIEVAL_UNAVAILABLE"
    record_search_fallback_unavailable(
        "recommend", metadata["fallback_reason_code"]
    )


__all__ = [
    "mark_candidate_unavailable",
    "mark_retrieval_unavailable",
    "mark_semantic_fallback",
    "mark_terminal_recommendation_fallback",
    "mark_terminal_search_fallback",
]
