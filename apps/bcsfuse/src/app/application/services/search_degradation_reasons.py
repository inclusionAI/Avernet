"""Stable public degradation reason codes for v1 search responses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def degraded_reasons(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Return v1's stable, ordered degradation reason codes."""

    reasons: list[str] = []
    for reason in (
        "semantic_unavailable",
        "keyword_bypassed",
        "keyword_unavailable",
        "literal_fallback_used",
        "literal_fallback_default",
    ):
        if metadata.get(reason) is True and reason not in reasons:
            reasons.append(reason)
    semantic_reason_code = metadata.get("semantic_reason_code")
    if (
        metadata.get("semantic_unavailable") is True
        and isinstance(semantic_reason_code, str)
        and semantic_reason_code
        and semantic_reason_code not in reasons
    ):
        reasons.insert(
            min(1, len(reasons)),
            semantic_reason_code,
        )
    if (
        metadata.get("semantic_unavailable") is True
        and metadata.get("keyword_search_used") is True
        and "keyword_fallback_used" not in reasons
    ):
        reasons.append("keyword_fallback_used")
    if (
        metadata.get("literal_fallback_default_used") is True
        and "literal_fallback_default" not in reasons
    ):
        reasons.append("literal_fallback_default")
    if (
        metadata.get("score_source") == "regex_fallback"
        and "regex_fallback" not in reasons
    ):
        reasons.append("regex_fallback")
    for reason in (
        "retrieval_unavailable",
        "candidate_unavailable",
        "fallback_unavailable",
    ):
        if metadata.get(reason) is True and reason not in reasons:
            reasons.append(reason)
    if (
        metadata.get("rerank_degraded") is True
        and "rerank_degraded" not in reasons
    ):
        reasons.append("rerank_degraded")
    for reason in ("local_snapshot_used", "last_known_good_used"):
        if metadata.get(reason) is True and reason not in reasons:
            reasons.append(reason)
    if metadata.get("index_complete") is False:
        reasons.append("index_incomplete")
    return tuple(reasons)


__all__ = ["degraded_reasons"]
