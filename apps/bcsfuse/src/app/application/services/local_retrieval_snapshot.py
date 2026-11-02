"""Project the zero-dependency local snapshot into search/advice outcomes."""

from __future__ import annotations

from typing import Any

from app.application.services.worker_vector_match_types import MatchResult
from app.domain.models.candidate_recommendation import CandidateRecommendation
from app.domain.models.metadata_record import MetadataRecord


def snapshot_hits(
    snapshot: Any,
    *,
    query: str,
    limit: int,
    filters: dict[str, Any],
) -> Any:
    """Use keyword matches first, then deterministic local defaults.

    The snapshot and its methods are process-owned. A read failure returns no
    hit rather than an incomplete result and is never treated as success.
    """

    keyword = _snapshot_call(
        lambda: snapshot.keyword_search(query, limit, filters, None)
    )
    if keyword:
        return keyword, "keyword"
    defaults = _snapshot_call(
        lambda: snapshot.default_search(limit, filters, None)
    )
    return defaults, "default"


def project_snapshot_hit(hit: Any) -> MatchResult:
    payload = dict(getattr(hit, "payload", None) or {})
    profile_key = _profile_key(hit, payload)
    score = _bounded_score(getattr(hit, "score", 0.0))
    return MatchResult(
        profile_key=profile_key,
        metadata=_metadata_record(profile_key, payload),
        score=score,
        aggregated_score=score,
        reasons=["local_snapshot"],
        fragment_matches=[],
        is_reranked=False,
    )


def project_snapshot_recommendation(hit: Any) -> CandidateRecommendation:
    payload = dict(getattr(hit, "payload", None) or {})
    profile_key = _profile_key(hit, payload)
    score = _bounded_score(getattr(hit, "score", 0.0))
    metadata = _metadata_record(profile_key, payload)
    return CandidateRecommendation(
        profile_key=profile_key,
        worker_id=metadata.staff_id,
        score=score,
        reasons=["local_snapshot"],
        domain=metadata.domains[0] if metadata.domains else "general",
        domain_confidence=0.4,
        matched_skills=metadata.active_skill_names,
        matched_contexts=[],
        is_supplement=True,
        short_profile=metadata.short_profile,
        trust_level=str(payload.get("trust_level") or ""),
    )




def direct_snapshot_hits(
    snapshot: Any,
    *,
    query: str,
    limit: int,
    filters: dict[str, Any],
) -> tuple[tuple[Any, ...], str]:
    """Execute a bounded exact-identifier snapshot path before Qdrant work.

    A direct target may contain multiple whitespace-separated identifiers. The
    snapshot is queried once per token with only the remaining result budget,
    so an explicit multi-agent lookup never expands into a full Qdrant scan.
    """

    exact_search = getattr(snapshot, "exact_search", None)
    if not callable(exact_search) or limit <= 0:
        return (), "miss"

    hits: list[Any] = []
    remaining = limit
    seen: set[str] = set()
    for token in query.split():
        if remaining <= 0:
            break
        token_hits = _snapshot_call(
            lambda token=token, remaining=remaining: exact_search(
                token, remaining, filters, None
            )
        )
        for hit in token_hits:
            hit_id = str(getattr(hit, "id", "") or len(seen))
            if hit_id in seen and hit_id:
                continue
            if hit_id:
                seen.add(hit_id)
            hits.append(hit)
            remaining -= 1
            if remaining <= 0:
                break
    return tuple(hits), "exact"

def snapshot_metadata(
    snapshot: Any,
    *,
    hit_count: int,
    strategy: str,
) -> dict[str, Any]:
    return {
        "local_snapshot_used": True,
        "local_snapshot_strategy": strategy,
        "local_snapshot_match_count": hit_count,
        "snapshot_source": getattr(snapshot, "source", None),
        "snapshot_index_version": getattr(
            snapshot,
            "snapshot_index_version",
            getattr(snapshot, "version", None),
        ),
        "snapshot_profile_count": getattr(snapshot, "profile_count", None),
        "snapshot_age_seconds": snapshot_age_seconds(snapshot),
    }


def direct_snapshot_metadata(
    snapshot: Any,
    *,
    hit_count: int,
    strategy: str,
) -> dict[str, Any]:
    """Publish exact direct lookup provenance without an outage marker."""

    metadata = snapshot_metadata(snapshot, hit_count=hit_count, strategy=strategy)
    metadata.pop("local_snapshot_used", None)
    return {
        **metadata,
        "direct_snapshot_used": True,
        "direct_snapshot_exact": strategy == "exact",
        "score_source": "direct_keyword",
    }


def snapshot_age_seconds(snapshot: Any) -> float | None:
    if getattr(snapshot, "source", None) == "file":
        saved_at = getattr(snapshot, "snapshot_saved_at_unix", 0.0)
        if saved_at > 0:
            from time import time

            return max(0.0, time() - float(saved_at))

    updated_at = getattr(snapshot, "updated_at", None)
    if updated_at is None:
        return None
    from time import monotonic

    return max(0.0, monotonic() - float(updated_at))


def _snapshot_call(operation: Any) -> Any:
    try:
        return operation()
    except Exception:
        return []


def _profile_key(hit: Any, payload: dict[str, Any]) -> str:
    explicit = payload.get("profile_key")
    if explicit:
        return str(explicit)
    return str(hit.id)


def _bounded_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.01
    return min(max(score, 0.01), 1.0)


def _metadata_record(profile_key: str, payload: dict[str, Any]) -> MetadataRecord:
    profile_id = str(payload.get("profile_id") or "default")
    worker_id = str(
        payload.get("worker_id")
        or payload.get("staff_id")
        or profile_key.rsplit(":", 1)[0]
    )
    short_profile = str(
        payload.get("short_profile")
        or payload.get("content_preview")
        or payload.get("content")
        or ""
    )[:120]
    return MetadataRecord(
        profile_key=profile_key,
        vector_id=None,
        staff_id=worker_id,
        profile_id=profile_id,
        profile_type=str(payload.get("profile_type") or "default"),
        domains=_string_list(payload.get("domains")),
        active_skill_names=_string_list(payload.get("active_skill_names")),
        suitable_roles=_string_list(payload.get("suitable_roles")),
        source_root=str(payload.get("source_root") or "local_snapshot"),
        payload=dict(payload),
        short_profile=short_profile,
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value]
    return [str(value)] if value not in (None, "") else []


__all__ = [
    "direct_snapshot_hits",
    "direct_snapshot_metadata",
    "project_snapshot_hit",
    "project_snapshot_recommendation",
    "snapshot_hits",
    "snapshot_metadata",
]
