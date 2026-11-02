"""V1 recommendation response projection and its latency instrumentation."""

from __future__ import annotations

from app.domain.models.bot_recommendation import (
    create_bot_recommendation_response,
)
from app.domain.models.candidate_recommendation import (
    CandidateRecommendationResponse,
)
from app.infra.public.observability.search_stage_metrics import (
    search_stage_metrics,
)


def build_v1_recommendation_response(
    candidate_response: CandidateRecommendationResponse,
    *,
    driver_bot_id: str | None = None,
    trace_id: str = "",
    query_type: str = "recommend",
):
    """Project a candidate response while recording its adapter stage."""

    with search_stage_metrics("recommend", "projection"):
        return create_bot_recommendation_response(
            candidate_response=candidate_response,
            driver_bot_id=driver_bot_id,
            trace_id=trace_id,
            query_type=query_type,
        )


__all__ = ["build_v1_recommendation_response"]
