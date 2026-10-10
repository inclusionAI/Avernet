"""Merge lexical and dense profile rankings without mixing raw score scales."""

import logging
from dataclasses import replace

from src.application.services.fragment_candidate_selection import (
    select_rerank_candidates,
)
from src.application.services.worker_vector_match_types import FragmentProfileCandidate
from src.domain.models.profile_fragment import FragmentMatch
from src.domain.models.vector_search_hit import VectorSearchHit
from src.domain.services.retrieval_logging import log_rows, log_stage
from src.domain.services.vector_store_adapter import VectorStoreAdapter

logger = logging.getLogger("src.application.services.worker_vector_match_service")


def keyword_candidates(
    store: VectorStoreAdapter,
    query: str,
    limit: int,
    filters: dict | None,
    excluded: set[str],
    allowed: set[str] | None,
    enabled_types: set[str],
) -> list[FragmentProfileCandidate]:
    if not query.strip() or limit <= 0 or allowed == set() or not enabled_types:
        return []
    try:
        fetch_limit, lookups = limit, 0
        while True:
            hits = store.text_search(query, fetch_limit, filters=filters)
            lookups += 1
            grouped = _group_keyword_hits(
                hits, filters, excluded, allowed, enabled_types
            )
            if len(grouped) >= limit or len(hits) < fetch_limit:
                break
            # The plugin limit counts physical fragments, not eligible profiles.
            # Re-read a larger ranked prefix; do not append repeated pages or
            # discard sibling fragments needed by downstream consumers.
            fetch_limit *= 2
        selected = list(grouped.values())[:limit]
        log_stage(
            logger,
            "keyword_search",
            hit_count=len(hits),
            candidate_count=len(selected),
            profile_budget=limit,
            fragment_fetch_limit=fetch_limit,
            lookups=lookups,
            available=True,
        )
        return selected
    except Exception as error:  # noqa: BLE001 -- Optional plugin read failure uses the documented dense fallback.
        log_stage(
            logger, "keyword_search", available=False, error_type=type(error).__name__
        )
        logger.warning(
            "[RETRIEVAL] keyword unavailable; continuing vector-only (%s)",
            type(error).__name__,
        )
        return []


def _group_keyword_hits(
    hits: list[VectorSearchHit],
    filters: dict | None,
    excluded: set[str],
    allowed: set[str] | None,
    enabled_types: set[str],
) -> dict[str, FragmentProfileCandidate]:
    grouped = {}
    for hit in hits:
        payload = hit.payload or {}
        # Providers must prefilter; also fail closed for legacy implementations.
        # Array payloads match when any element matches, just like MatchAny
        # and MatchValue in the provider. Strings remain exact scalar matches.
        if any(
            not any(
                actual in (v if isinstance(v, list) else [v])
                for actual in (
                    payload[k] if isinstance(payload.get(k), list) else [payload.get(k)]
                )
            )
            for k, v in (filters or {}).items()
        ):
            continue
        kind = payload.get("fragment_type", "full")
        if kind not in enabled_types:
            continue
        worker, profile = payload.get("worker_id"), payload.get("profile_id")
        key = payload.get("profile_key") or (
            f"{worker}:{profile}" if worker and profile else None
        )
        if not key or key in excluded or (allowed is not None and key not in allowed):
            continue
        candidate = grouped.setdefault(
            key, FragmentProfileCandidate(key, 0.0, [], dict(payload))
        )
        if payload.get("_keyword_exact_id"):
            candidate.metadata["_keyword_exact_id"] = True
        candidate.fragments.append(
            FragmentMatch(
                fragment_type=kind,
                fragment_id=hit.id,
                score=0.0,
                content=str(
                    payload.get("content")
                    or payload.get("searchable_text")
                    or payload.get("content_preview")
                    or ""
                ),
            )
        )
    return grouped


def fuse_candidates(
    dense: list[FragmentProfileCandidate],
    lexical: list[FragmentProfileCandidate],
    budget: int,
    vector_min_score: float,
) -> list[FragmentProfileCandidate]:
    """Normalized equal-weight RRF(k=60); exact worker ID has score 1.

    Dense eligibility uses its original score, not the fused score. Keyword-only
    candidates are never tested against a cosine threshold. At most budget
    unique profiles are returned in the same order used for no-rerank fallback.
    """
    dense, _ = select_rerank_candidates(
        [c for c in dense if c.aggregated_score >= vector_min_score],
        budget,
    )
    lexical = lexical[:budget]
    dense_rank = {c.profile_key: i for i, c in enumerate(dense, 1)}
    lexical_rank = {c.profile_key: i for i, c in enumerate(lexical, 1)}
    merged = {c.profile_key: c for c in dense}
    for candidate in lexical:
        previous = merged.get(candidate.profile_key)
        if previous is None:
            merged[candidate.profile_key] = candidate
        else:
            ids = {f.fragment_id for f in previous.fragments}
            merged[candidate.profile_key] = replace(
                previous,
                metadata={**previous.metadata, **candidate.metadata},
                fragments=previous.fragments
                + [f for f in candidate.fragments if f.fragment_id not in ids],
            )
    results = []
    for key, candidate in merged.items():
        score = (
            0.99
            * 61
            / 2
            * sum(
                1 / (60 + ranks[key])
                for ranks in (dense_rank, lexical_rank)
                if key in ranks
            )
        )
        if candidate.metadata.get("_keyword_exact_id"):
            score = 1.0
        results.append(replace(candidate, aggregated_score=score))
    results.sort(key=lambda c: (-c.aggregated_score, c.profile_key))
    log_stage(
        logger,
        "hybrid_selection",
        dense_count=len(dense),
        keyword_count=len(lexical),
        unique_count=len(results),
        selected_count=min(len(results), budget),
        budget=budget,
    )
    log_rows(
        logger,
        "hybrid_decisions",
        ["profile_key", "dense_rank", "keyword_rank", "score", "selected"],
        (
            [
                c.profile_key,
                dense_rank.get(c.profile_key),
                lexical_rank.get(c.profile_key),
                round(c.aggregated_score, 6),
                i < budget,
            ]
            for i, c in enumerate(results)
        ),
    )
    return results[:budget]
