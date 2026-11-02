"""Cold search requests must expose queue/cache/core/projection stage latency."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.responses import PlainTextResponse
from prometheus_client import REGISTRY

from app.adapters.http.search_response_projection import (
    build_v1_search_response,
)
from app.application.agent_search_service import (
    AgentSearchCommand,
    AgentSearchOutcome,
    AgentSearchService,
)
from app.bootstrap.oss_business_support import SearchRequestOSS
from app.bootstrap.search_capacity import (
    SearchCapacityMiddleware,
    SearchCapacitySettings,
)


def metric_count(operation: str, stage: str) -> float:
    return float(
        REGISTRY.get_sample_value(
            "bcsfuse_search_stage_duration_seconds_count",
            {"operation": operation, "stage": stage},
        )
        or 0.0
    )


def test_cache_and_projection_stages_are_recorded_separately() -> None:
    class Core:
        def embed(self, query: str) -> list[float]:
            return [0.1]

        def match(self, **kwargs: Any) -> list[dict[str, Any]]:
            return []

    service = AgentSearchService(
        embedding_generator=Core(),
        vector_match_service=Core(),
    )
    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="stage cold", top_k=1))
    )
    request = SearchRequestOSS(query="stage cold", top_k=1)
    response = build_v1_search_response(
        request, outcome, elapsed_ms=outcome.elapsed_ms
    )
    assert response["results_count"] == 0

    assert metric_count("search", "cache") >= 1.0
    assert metric_count("search", "projection") >= 1.0


def test_admission_queue_wait_is_also_exposed_as_a_unified_search_stage() -> None:
    async def app(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    middleware = SearchCapacityMiddleware(
        app,
        SearchCapacitySettings(max_inflight=1, retry_after_seconds=1.0),
    )

    before_queue = metric_count("search", "queue")
    before_retrieval = metric_count("search", "retrieval")

    async def call() -> None:
        await middleware(
            {
                "type": "http",
                "path": "/api/v1/search",
                "method": "POST",
                "headers": [(b"content-length", b"0")],
            },
            _receive,
            _send,
        )

    async def scenario() -> None:
        await call()
        await call()

    asyncio.run(scenario())
    assert metric_count("search", "queue") > before_queue
    assert metric_count("search", "retrieval") == before_retrieval


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _send(message):
    if message["type"] == "http.response.start":
        assert message["status"] == 200


def test_recommendation_projection_is_recorded_as_its_own_stage() -> None:
    from app.adapters.http.bot_recommendation_projection import (
        build_v1_recommendation_response,
    )
    from app.domain.models.candidate_recommendation import (
        CandidateRecommendationResponse,
    )
    from app.domain.models.retrieval_mode import RetrievalMode

    before = metric_count("recommend", "projection")
    response = build_v1_recommendation_response(
        CandidateRecommendationResponse(
            question="recommend projection",
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            metadata={},
        ),
        trace_id="trace-stage",
    )
    assert response.trace_id == "trace-stage"
    assert metric_count("recommend", "projection") > before
