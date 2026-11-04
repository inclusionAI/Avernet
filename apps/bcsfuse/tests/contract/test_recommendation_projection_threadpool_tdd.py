"""TDD: v1 recommendation projection must not run sync storage work on the loop."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_recommend_trust_enrichment_runs_in_search_worker_thread(monkeypatch):
    """SQLite-backed trust enrichment must move off the event loop."""

    from app.adapters.http import recommend_routes
    from app.adapters.http.dependencies import worker_dependencies
    from app.domain.models.bot_recommendation import BotRecommendationRequest
    from app.domain.models.candidate_recommendation import (
        CandidateRecommendation,
        CandidateRecommendationResponse,
    )
    from app.domain.models.retrieval_mode import RetrievalMode
    from app.application.agent_search_service import AgentRecommendationOutcome

    observed_threads: list[threading.Thread] = []

    class RecordingRegistryStore:
        def get_by_id(self, worker_id: str):
            observed_threads.append(threading.current_thread())
            time.sleep(0.002)
            return SimpleNamespace(
                state=SimpleNamespace(trust_level=SimpleNamespace(value="trusted"))
            )

    monkeypatch.setattr(
        worker_dependencies,
        "get_registry_store",
        lambda: RecordingRegistryStore(),
    )
    monkeypatch.setattr(
        "app.bootstrap.oss_business_routes.require_oss_auth",
        lambda request: None,
    )

    class CapturedAgentSearchService:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def recommend(self, command):
            assert command.question == "python search agent"
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

    app_state = SimpleNamespace(agent_search_result_cache=None)
    http_request = SimpleNamespace(headers={}, state=app_state)
    request = BotRecommendationRequest(
        question="python search agent",
        topK=1,
        enable_rerank=False,
    )

    response = await recommend_routes.recommend_bots(http_request, request)

    assert response.recommendations[0].profile_tags["trust_level"] == "trusted"
    assert observed_threads
    assert observed_threads[0] is not threading.main_thread()


def test_recommend_trust_enrichment_batches_unique_worker_lookups(monkeypatch):
    """Trust enrichment must avoid one durable registry read per recommendation."""
    from app.adapters.http.recommend_routes import _enrich_trust_level
    from app.domain.models.bot_recommendation import BotRecommendation

    class RecordingRegistryStore:
        def __init__(self) -> None:
            self.batch_calls: list[list[str]] = []

        def get_by_ids(self, worker_ids):
            self.batch_calls.append(worker_ids)
            return {
                worker_id: SimpleNamespace(
                    state=SimpleNamespace(trust_level=SimpleNamespace(value="trusted"))
                )
                for worker_id in worker_ids
            }

    def recommendation(worker_id: str, index: int) -> BotRecommendation:
        return BotRecommendation(
            profile_key=f"bot:{worker_id}:default:{index}",
            worker_id=worker_id,
            score=0.9 - index * 0.01,
        )

    store = RecordingRegistryStore()
    response = SimpleNamespace(
        recommendations=[
            recommendation("python", 0),
            recommendation("payments", 1),
            recommendation("python", 2),
        ]
    )

    from app.adapters.http.dependencies import worker_dependencies
    monkeypatch.setattr(
        "app.adapters.http.dependencies.worker_dependencies.get_registry_store",
        lambda: store,
    )
    _enrich_trust_level(response)

    assert store.batch_calls == [["python", "payments"]]
    assert [rec.profile_tags["trust_level"] for rec in response.recommendations] == [
        "trusted",
        "trusted",
        "trusted",
    ]


def test_recommend_trust_enrichment_falls_back_for_legacy_registry_stores(monkeypatch):
    """Old registry implementations without batch reads still receive enrichments."""
    from app.adapters.http.recommend_routes import _enrich_trust_level
    from app.domain.models.bot_recommendation import BotRecommendation

    class LegacyRegistryStore:
        def __init__(self) -> None:
            self.single_calls: list[str] = []

        def get_by_id(self, worker_id: str):
            self.single_calls.append(worker_id)
            return SimpleNamespace(
                state=SimpleNamespace(trust_level=SimpleNamespace(value="legacy"))
            )

    store = LegacyRegistryStore()
    response = SimpleNamespace(
        recommendations=[
            BotRecommendation(profile_key="bot:python:default", worker_id="python", score=0.9),
            BotRecommendation(profile_key="bot:python:extra", worker_id="python", score=0.8),
        ]
    )

    from app.adapters.http.dependencies import worker_dependencies
    monkeypatch.setattr(
        "app.adapters.http.dependencies.worker_dependencies.get_registry_store",
        lambda: store,
    )
    _enrich_trust_level(response)

    assert store.single_calls == ["python"]
    assert all(
        rec.profile_tags["trust_level"] == "legacy"
        for rec in response.recommendations
    )
