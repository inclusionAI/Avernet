"""Build final fallback outcomes without owning request admission policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.application.services.query_normalizer import normalize_query_text
from app.application.services.local_retrieval_snapshot import (
    project_snapshot_hit,
    project_snapshot_recommendation,
    snapshot_hits,
    snapshot_metadata,
)
from app.domain.models.candidate_recommendation import (
    CandidateRecommendationResponse,
)
from app.domain.models.retrieval_mode import RetrievalMode
from app.infra.public.observability.search_dependency_metrics import (
    record_search_fallback_unavailable,
)


MAX_RECOMMEND_EXPAND_FACTOR = 3


async def execute_final_outcome_offloaded(
    *,
    final_outcome: Callable[..., Any],
    run: Callable[[Callable[[], Any], float | None], Awaitable[Any]],
    command: Any,
    error_type: str,
    timeout_seconds: float | None,
    local_snapshot: Any | None,
) -> Any:
    """Run terminal fallback work away from the serving event loop."""

    return await run(
        lambda: final_outcome(
            command,
            error_type=error_type,
            local_snapshot=local_snapshot,
        ),
        timeout_seconds,
    )


def build_final_search_outcome(
    command: Any,
    *,
    error_type: str,
    local_snapshot: Any | None,
    filters: dict[str, Any],
    outcome_type: Callable[..., Any],
    publish_status: Callable[[dict[str, Any]], None],
) -> Any:
    metadata: dict[str, Any] = {
        "retrieval_unavailable": True,
        "retrieval_error": error_type,
    }
    runtime_config = {"reranker_model": command.reranker_model, "filters": filters}
    results: tuple[Any, ...] = ()
    try:
        if local_snapshot is not None:
            hits, strategy = snapshot_hits(
                local_snapshot,
                query=normalize_query_text(command.query),
                limit=command.top_k,
                filters=filters,
            )
            results = tuple(project_snapshot_hit(hit) for hit in hits)
            if results:
                metadata.update(
                    snapshot_metadata(
                        local_snapshot,
                        hit_count=len(results),
                        strategy=strategy,
                    )
                )
                metadata.pop("fallback_unavailable", None)
    except Exception:
        results = ()
    if not results:
        metadata["fallback_unavailable"] = True
        metadata["fallback_reason_code"] = "SEARCH_RETRIEVAL_UNAVAILABLE"
        record_search_fallback_unavailable(
            "search", metadata["fallback_reason_code"]
        )
    publish_status(metadata)
    return outcome_type(
        query=command.query,
        normalized_query=normalize_query_text(command.query),
        top_k=command.top_k,
        mode=command.mode,
        results=results,
        metadata=metadata,
        config_applied=runtime_config,
        elapsed_ms=0.0,
    )


def build_final_recommendation_outcome(
    command: Any,
    *,
    error_type: str,
    local_snapshot: Any | None,
    filters: dict[str, Any],
    response_type: Callable[..., Any],
    outcome_type: Callable[..., Any],
    publish_status: Callable[[dict[str, Any]], None],
) -> Any:
    metadata: dict[str, Any] = {
        "candidate_unavailable": True,
        "candidate_error": error_type,
    }
    recommendations: list[Any] = []
    try:
        if local_snapshot is not None:
            hits, strategy = snapshot_hits(
                local_snapshot,
                query=normalize_query_text(command.question),
                limit=command.top_k,
                filters=filters,
            )
            recommendations = [
                project_snapshot_recommendation(hit) for hit in hits
            ]
            if recommendations:
                metadata.update(
                    snapshot_metadata(
                        local_snapshot,
                        hit_count=len(recommendations),
                        strategy=strategy,
                    )
                )
                metadata["candidate_source"] = "local_snapshot"
    except Exception:
        recommendations = []
    if not recommendations:
        metadata["fallback_unavailable"] = True
        metadata["fallback_reason_code"] = "CANDIDATE_RETRIEVAL_UNAVAILABLE"
        metadata["candidate_source"] = "fallback_unavailable"
        record_search_fallback_unavailable(
            "recommend", metadata["fallback_reason_code"]
        )
    else:
        metadata["candidate_source"] = "local_snapshot"
    publish_status(metadata)
    response = response_type(
        question=command.question,
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        metadata=metadata,
        recommendations=recommendations,
        total_candidates=len(recommendations),
        selected_candidates=len(recommendations),
    )
    return outcome_type(
        question=command.question,
        response=response,
        config_applied={
            "expand_factor": min(
                command.expand_factor, MAX_RECOMMEND_EXPAND_FACTOR
            ),
            "reranker_model": (
                None if not command.enable_rerank else command.reranker_model
            ),
        },
        elapsed_ms=0.0,
        diagnostics={
            "min_score_explicit": command.min_score_explicit,
            "vector_min_score": command.vector_min_score,
            "rerank_min_score": command.rerank_min_score,
        },
        metadata=dict(metadata),
    )


__all__ = [
    "build_final_recommendation_outcome",
    "build_final_search_outcome",
]
