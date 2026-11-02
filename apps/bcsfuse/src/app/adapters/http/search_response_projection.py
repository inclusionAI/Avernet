"""V1 search response projection kept independent of HTTP route policy."""

from __future__ import annotations

from typing import Any

from app.application.agent_search_service import (
    AgentSearchCommand,
    AgentSearchOutcome,
    AgentSearchService,
    default_search_filters,
    degraded_reasons,
)
from app.application.services.search_index_state import project_index_state
from app.infra.public.observability.search_stage_metrics import (
    search_stage_metrics,
)
from app.bootstrap.oss_business_routes import SearchRequestOSS


def build_v1_search_response(
    request: SearchRequestOSS,
    outcome: AgentSearchOutcome,
    *,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Project one application search outcome and record its stage time."""

    with search_stage_metrics("search", "projection"):
        return _build_v1_search_response(
            request, outcome, elapsed_ms=elapsed_ms
        )


def _build_v1_search_response(
    request: SearchRequestOSS,
    outcome: AgentSearchOutcome,
    *,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Project one application search outcome into the stable v1 schema."""

    reasons = degraded_reasons(outcome.metadata)
    status = (
        "fallback_unavailable"
        if outcome.metadata.get("fallback_unavailable") is True
        else "degraded"
        if reasons
        else "ok"
    )
    response: dict[str, Any] = {
        "query": request.query,
        "status": status,
        "reasons": list(reasons),
        "top_k": request.top_k,
        "mode": request.mode,
        "results_count": len(outcome.results),
        "results": [
            _project_result(result, outcome=outcome)
            for result in outcome.results
        ],
        "timing_ms": round(elapsed_ms, 2),
        "config_applied": outcome.config_applied,
    }
    index_state = project_index_state(outcome.metadata)
    if index_state:
        response["index_state"] = index_state
    fallback_reason_code = outcome.metadata.get("fallback_reason_code")
    if isinstance(fallback_reason_code, str) and fallback_reason_code:
        response["fallback_reason_code"] = fallback_reason_code
    semantic_status_code = outcome.metadata.get("semantic_status_code")
    if semantic_status_code is not None:
        response["semantic_status_code"] = int(semantic_status_code)
    retry_after = outcome.metadata.get("semantic_retry_after_seconds")
    if retry_after is not None:
        response["semantic_retry_after_seconds"] = float(retry_after)
    provider_reason_code = outcome.metadata.get("semantic_provider_reason_code")
    if isinstance(provider_reason_code, str) and provider_reason_code:
        response["semantic_provider_reason_code"] = provider_reason_code
    return response


def build_final_v1_search_response(
    request: SearchRequestOSS,
    *,
    elapsed_ms: float,
    error_type: str,
    local_snapshot: Any | None = None,
) -> dict[str, Any]:
    """Build the final search envelope without executing result projection."""

    command = AgentSearchCommand(
        query=request.query,
        top_k=request.top_k,
        mode=getattr(request, "mode", "auto"),
        min_score=getattr(request, "min_score", 0.0),
        filters=request.filters,
        reranker_model=getattr(request, "reranker_model", None),
    )
    outcome = AgentSearchService.final_search_outcome(
        command,
        error_type=error_type,
        local_snapshot=local_snapshot,
    )
    response: dict[str, Any] = {
        "query": request.query,
        "status": outcome.metadata["status"],
        "reasons": list(degraded_reasons(outcome.metadata)),
        "top_k": request.top_k,
        "mode": outcome.mode,
        "results_count": len(outcome.results),
        "results": [
            _project_result(result, outcome=outcome)
            for result in outcome.results
        ],
        "timing_ms": round(elapsed_ms, 2),
        "config_applied": {
            "reranker_model": command.reranker_model,
            "filters": default_search_filters(command.filters),
        },
    }
    index_state = project_index_state(outcome.metadata)
    if index_state:
        response["index_state"] = index_state
    fallback_reason_code = outcome.metadata.get("fallback_reason_code")
    if isinstance(fallback_reason_code, str) and fallback_reason_code:
        response["fallback_reason_code"] = fallback_reason_code
    retrieval_error = outcome.metadata.get("retrieval_error")
    if isinstance(retrieval_error, str) and retrieval_error:
        response["retrieval_error"] = retrieval_error
    return response


def _project_result(
    result: Any,
    *,
    outcome: AgentSearchOutcome,
) -> dict[str, Any]:
    parts = result.profile_key.split(":")
    metadata = getattr(result, "metadata", None)
    short_profile = ""
    if metadata is not None:
        short_profile = getattr(metadata, "short_profile", "")
        if not short_profile:
            short_profile = getattr(metadata, "payload", {}).get(
                "short_profile", ""
            )
    reasons = getattr(result, "reasons", None) or []
    source = "vector"
    if (
        outcome.metadata.get("semantic_unavailable") is True
        and outcome.metadata.get("keyword_search_used") is True
    ):
        source = "keyword_fallback"
    if "local_snapshot" in reasons:
        source = "local_snapshot"
    elif "literal_fallback_default" in reasons:
        source = "literal_fallback_default"
    elif "literal_fallback" in reasons:
        source = "literal_fallback"
    return {
        "profile_key": result.profile_key,
        "worker_id": (
            ":".join(parts[:-1]) if len(parts) > 1 else result.profile_key
        ),
        "profile_id": parts[-1] if len(parts) > 1 else "default",
        "score": result.score,
        "aggregated_score": getattr(result, "aggregated_score", None),
        "fragment_matches": [
            _project_fragment(fragment)
            for fragment in (getattr(result, "fragment_matches", None) or [])[:5]
        ],
        "short_profile": short_profile,
        "source": source,
    }


def _project_fragment(fragment: Any) -> dict[str, Any]:
    content = (
        getattr(fragment, "content", "")
        or getattr(fragment, "content_preview", "")
    )
    return {
        "fragment_type": getattr(fragment, "fragment_type", "unknown"),
        "score": getattr(fragment, "score", 0.0),
        "weighted_score": getattr(fragment, "weighted_score", 0.0),
        "content_preview": getattr(fragment, "content_preview", "")[:100],
        "content": content,
    }


__all__ = ["build_final_v1_search_response", "build_v1_search_response"]
