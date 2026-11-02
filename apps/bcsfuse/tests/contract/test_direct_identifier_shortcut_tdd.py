"""TDD contract for exact identifiers embedded in natural searches."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from search_core.plugin_api.vector_search_hit import VectorSearchHit

from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchService,
)
from app.application.services.query_intent_policy import resolve_query_intent
from app.application.services.query_normalizer import normalize_query_text


@dataclass
class ExactSnapshot:
    hits: dict[str, VectorSearchHit]
    calls: list[tuple[str, int]] = field(default_factory=list)

    def exact_search(self, query, limit, filters, allowed_ids=None):
        self.calls.append((str(query), int(limit)))
        hit = self.hits.get(str(query))
        return [hit] if hit is not None else []


@dataclass
class RecordingCore:
    embedding_calls: list[str] = field(default_factory=list)
    match_calls: list[dict[str, Any]] = field(default_factory=list)

    def embed(self, query: str) -> list[float]:
        self.embedding_calls.append(query)
        return [0.5]

    def match(self, **kwargs: Any) -> list[Any]:
        self.match_calls.append(kwargs)
        return [
            {
                "profile_key": "semantic-match:default",
                "score": 0.9,
            }
        ]


def _snapshot(work: str) -> ExactSnapshot:
    return ExactSnapshot(
        {
            work: VectorSearchHit(
                id=f"{work}:default",
                score=1.0,
                payload={
                    "profile_key": f"{work}:default",
                    "worker_id": work,
                    "profile_id": "default",
                    "content": f"Agent {work}",
                },
            )
        }
    )


def test_query_with_embedded_identifier_uses_direct_route() -> None:
    query = "find payment expert load20261102_0331"
    intent = resolve_query_intent(normalize_query_text(query))

    assert intent.route == "direct_keyword"
    assert intent.reason == "embedded_identifier"
    assert intent.target == "load20261102_0331"


def test_direct_search_uses_local_snapshot_without_embedding_or_qdrant() -> None:
    query = "find load20261102_0331 payment-graph specialist"
    snapshot = _snapshot("load20261102_0331")
    core = RecordingCore()
    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        candidate_recommendation_service=core,
        local_snapshot=snapshot,
    )

    outcome = asyncio.run(service.search(AgentSearchCommand(query=query, top_k=1)))

    assert core.embedding_calls == []
    assert core.match_calls == []
    assert snapshot.calls == [("load20261102_0331", 1)]
    assert len(outcome.results) == 1
    assert outcome.results[0].profile_key == "load20261102_0331:default"
    assert outcome.metadata["query_route"] == "direct_keyword"
    assert outcome.metadata["query_intent"] == "embedded_identifier"
    assert outcome.config_applied["direct_snapshot_used"] is True


def test_multiple_embedded_identifiers_stay_vertex_budgeted() -> None:
    query = "load20261102_0331 leader load20261102_0379 employee"
    snapshot = ExactSnapshot(
        {
            work: VectorSearchHit(
                id=f"{work}:default",
                score=1.0,
                payload={
                    "profile_key": f"{work}:default",
                    "worker_id": work,
                    "profile_id": "default",
                    "content": f"Agent {work}",
                },
            )
            for work in ("load20261102_0331", "load20261102_0379")
        }
    )
    core = RecordingCore()
    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        candidate_recommendation_service=core,
        local_snapshot=snapshot,
    )

    outcome = asyncio.run(service.search(AgentSearchCommand(query=query, top_k=2)))

    assert core.embedding_calls == []
    assert core.match_calls == []
    assert [call[1] for call in snapshot.calls] == [2, 1]
    assert [result.profile_key for result in outcome.results] == [
        "load20261102_0331:default",
        "load20261102_0379:default",
    ]


class NeverCalledRecommendationService:
    def recommend(self, **kwargs: Any) -> Any:
        raise AssertionError("direct recommendation must not call the core")


def test_direct_recommendation_uses_snapshot_without_embedding() -> None:
    query = "find load20261102_0331 payment expert"
    snapshot = _snapshot("load20261102_0331")
    service = AgentSearchService(
        candidate_recommendation_service=NeverCalledRecommendationService(),
        local_snapshot=snapshot,
    )

    outcome = asyncio.run(
        service.recommend(
            AgentRecommendationCommand(
                question=query,
                top_k=1,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=1,
                vector_min_score=0.01,
                rerank_min_score=None,
                min_score_explicit=False,
            )
        )
    )

    assert snapshot.calls == [("load20261102_0331", 1)]
    assert outcome.response.recommendations[0].worker_id == "load20261102_0331"
    assert outcome.response.metadata["query_intent"] == "embedded_identifier"
