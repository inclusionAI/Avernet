"""TDD for latency attribution of late recommendation route stages."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace


def _fake_stage_context(observed):
    from app.infra.public.observability.search_stage_metrics import (
        search_stage_metrics,
    )

    def recorder(operation, stage):
        observed.append((operation, stage))
        return nullcontext()

    return recorder


def test_recommendation_late_stages_are_independently_profiled(monkeypatch):
    from app.adapters.http import recommend_routes
    from app.adapters.http.dependencies import worker_dependencies
    from app.domain.models.bot_recommendation import BotRecommendationRequest
    from app.domain.models.candidate_recommendation import (
        CandidateRecommendation,
        CandidateRecommendationResponse,
    )
    from app.domain.models.retrieval_mode import RetrievalMode
    from app.application.agent_search_service import AgentRecommendationOutcome

    observed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        recommend_routes,
        "search_stage_metrics",
        _fake_stage_context(observed),
        raising=False,
    )
    monkeypatch.setattr(
        worker_dependencies,
        "get_registry_store",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.bootstrap.oss_business_routes.require_oss_auth",
        lambda request: None,
    )

    class CapturedAgentSearchService:
        def __init__(self, **kwargs):
            pass

        async def recommend(self, command):
            return AgentRecommendationOutcome(
                question=command.question,
                response=CandidateRecommendationResponse(
                    question=command.question,
                    mode=RetrievalMode.EXPERT_DIAGNOSIS,
                    recommendations=[
                        CandidateRecommendation(
                            profile_key="bot:python:default",
                            worker_id="bot:python",
                            score=0.8,
                            reasons=["python"],
                            short_profile="Python agent",
                        )
                    ],
                    total_candidates=1,
                    selected_candidates=1,
                ),
                config_applied={"expand_factor": command.expand_factor},
                elapsed_ms=1.0,
            )

    monkeypatch.setattr(
        recommend_routes,
        "AgentSearchService",
        CapturedAgentSearchService,
    )
    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        lambda: object(),
    )

    request = BotRecommendationRequest(
        question="python search agent",
        topK=1,
        enable_rerank=False,
    )
    app_state = SimpleNamespace(agent_search_result_cache=None)
    response = asyncio.run(
            recommend_routes.recommend_bots(
                SimpleNamespace(headers={}, state=app_state), request
            )
        )

    assert response.recommendations
    assert ("recommend", "trust_enrichment") in observed
    assert ("recommend", "cache_publication") in observed
