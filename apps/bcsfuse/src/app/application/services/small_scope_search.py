"""Policy and provenance metadata for the small-corpus fast path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.services.local_retrieval_snapshot import snapshot_metadata


@dataclass(frozen=True, slots=True)
class SmallScopePolicy:
    """Bound when process-local worker-card matching is trusted."""

    max_profile_count: int = 20
    max_query_length: int = 100
    min_confidence: float = 0.30
    enabled_modes: tuple[str, ...] = ("auto", "fast")


DEFAULT_SMALL_SCOPE_POLICY = SmallScopePolicy()


def small_scope_allows(
    policy: SmallScopePolicy,
    *,
    query: str,
    snapshot: Any,
    mode: str,
) -> bool:
    """Return whether a zero-dependency local match may be attempted."""

    if mode not in policy.enabled_modes or snapshot is None:
        return False
    if len(query) > policy.max_query_length:
        return False
    profile_count = getattr(snapshot, "profile_count", 0)
    if not isinstance(profile_count, int):
        return False
    return profile_count <= policy.max_profile_count


def small_scope_evaluate(
    policy: SmallScopePolicy,
    *,
    hit_count: int,
    best_score: float,
    required_score: float,
) -> tuple[bool, str | None]:
    """Decide whether a local hit can be published without semantic fallback."""

    if hit_count <= 0:
        return False, "empty"
    if best_score < policy.min_confidence:
        return False, "low_confidence"
    if best_score < required_score:
        return False, "low_score_or_threshold"
    return True, None


def small_scope_config(
    *,
    reject_reason: str | None,
) -> dict[str, Any]:
    """Publish the routing decision to the transport-independent runtime view."""

    accepted = reject_reason is None
    config: dict[str, Any] = {
        "small_scope_attempted": True,
        "small_scope_used": accepted,
        "small_scope_rejected": not accepted,
    }
    if accepted:
        config["candidate_source"] = "small_scope_snapshot"
        config["score_source"] = "small_scope_snapshot"
    return config


def small_scope_attempt(
    policy: SmallScopePolicy,
    snapshot: Any,
    *,
    hit_count: int,
    best_score: float,
    required_score: float,
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    """Evaluate one local attempt and return its full provenance payload."""

    accepted, reject_reason = small_scope_evaluate(
        policy,
        hit_count=hit_count,
        best_score=best_score,
        required_score=required_score,
    )
    metadata = (
        small_scope_metadata(
            snapshot, hit_count=hit_count, best_score=best_score
        )
        if accepted
        else small_scope_rejection_metadata(
            snapshot,
            hit_count=hit_count,
            best_score=best_score,
            reject_reason=reject_reason or "low_score_or_threshold",
        )
    )
    return accepted, metadata, small_scope_config(reject_reason=reject_reason)


def _small_scope_base_metadata(
    snapshot: Any,
    *,
    hit_count: int,
    best_score: float,
) -> dict[str, Any]:
    metadata = snapshot_metadata(
        snapshot, hit_count=hit_count, strategy="keyword"
    )
    metadata.pop("local_snapshot_used", None)
    return {
        **metadata,
        "small_scope_attempted": True,
        "small_scope_best_score": best_score,
    }


def small_scope_metadata(
    snapshot: Any,
    *,
    hit_count: int,
    best_score: float,
) -> dict[str, Any]:
    """Publish local fast-path provenance and confidence."""

    return {
        **_small_scope_base_metadata(
            snapshot, hit_count=hit_count, best_score=best_score
        ),
        "small_scope_used": True,
        "small_scope_rejected": False,
        "candidate_source": "small_scope_snapshot",
        "score_source": "small_scope_snapshot",
    }


def small_scope_rejection_metadata(
    snapshot: Any,
    *,
    hit_count: int,
    best_score: float,
    reject_reason: str,
) -> dict[str, Any]:
    """Explain why the local attempt continued to semantic retrieval."""

    if reject_reason not in {"empty", "low_confidence", "low_score_or_threshold"}:
        raise ValueError(f"unsupported small scope reject reason: {reject_reason}")
    return {
        **_small_scope_base_metadata(
            snapshot, hit_count=hit_count, best_score=best_score
        ),
        "small_scope_used": False,
        "small_scope_rejected": True,
        "small_scope_reject_reason": reject_reason,
        "small_scope_fallback_to_vector": True,
    }


__all__ = [
    "DEFAULT_SMALL_SCOPE_POLICY",
    "SmallScopePolicy",
    "small_scope_allows",
    "small_scope_attempt",
    "small_scope_config",
    "small_scope_evaluate",
    "small_scope_metadata",
    "small_scope_rejection_metadata",
]
