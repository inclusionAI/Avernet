"""TDD contracts for zero-embedding direct lookup availability and speed."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from search_core.plugin_api.vector_point import VectorPoint

from app.application.agent_search_service import (
    AgentRecommendationCommand,
    AgentSearchCommand,
    AgentSearchService,
)
from app.application.services.literal_fallback_maintenance import (
    LiteralFallbackSnapshot,
)
from app.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from app.domain.models.metadata_record import MetadataRecord
from app.domain.models.vector_search_hit import VectorSearchHit
from app.application.services.worker_vector_match_types import MatchResult
from app.infra.metadatastores.file_metadata_store_adapter import (
    FileMetadataStoreAdapter,
)


def _snapshot_point() -> VectorPoint:
    return VectorPoint(
        id="worker:default:full",
        vector=[],
        payload={
            "profile_key": "worker:default",
            "worker_id": "worker",
            "profile_id": "default",
            "availability": "public",
            "runtime_state": "online",
            "content": "payment writer",
        },
    )


def _snapshot() -> LiteralFallbackSnapshot:
    snapshot = LiteralFallbackSnapshot()
    snapshot.upsert([_snapshot_point()])
    return snapshot


def _direct_hit_store() -> Any:
    class DirectHitStore:
        def __init__(self) -> None:
            self.text_search_calls: list[Any] = []

        # keyword_candidates_with_status calls text_search positionally.
        def text_search(self, *args: Any, **kwargs: Any) -> list[VectorSearchHit]:
            self.text_search_calls.append((args, kwargs))
            return [
                VectorSearchHit(
                    id="worker:default:full",
                    score=1.0,
                    payload={
                        "profile_key": "worker:default",
                        "worker_id": "worker",
                        "profile_id": "default",
                        "fragment_type": "full",
                        "availability": "public",
                        "runtime_state": "online",
                        "content": "payment writer",
                    },
                )
            ]

    return DirectHitStore()


def _vector_service(
    store: Any,
    tmp_path: Any,
) -> WorkerVectorMatchService:
    return WorkerVectorMatchService(
        vector_store=store,
        metadata_store=FileMetadataStoreAdapter(
            storage_dir=str(tmp_path / "metadata")
        ),
        fragment_config=FragmentRetrievalConfig(enable_fragment_embedding=True),
    )


def test_direct_keyword_retrieval_is_not_semantic_outage(tmp_path: Any) -> None:
    store = _direct_hit_store()
    service = _vector_service(store, tmp_path)
    runtime_config: dict[str, Any] = {"query_route": "direct_keyword"}

    results = service.match(
        query_embedding=[],
        query="worker:default",
        mode="fragment",
        top_k=3,
        vector_min_score=0.0,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["keyword_search_used"] is True
    assert diagnostics["semantic_unavailable"] is False
    assert diagnostics["score_source"] == "direct_keyword"


def test_semantic_outage_still_reports_keyword_fallback(tmp_path: Any) -> None:
    store = _direct_hit_store()
    service = _vector_service(store, tmp_path)
    runtime_config: dict[str, Any] = {"query_route": "semantic"}

    results = service.match(
        query_embedding=[],
        query="payment writer",
        mode="fragment",
        top_k=3,
        vector_min_score=0.0,
        runtime_config=runtime_config,
    )

    assert results != []
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["score_source"] == "hybrid_rrf"


def _direct_match_result() -> MatchResult:
    return MatchResult(
        profile_key="worker:default",
        metadata=MetadataRecord(
            profile_key="worker:default",
            vector_id="worker:default:full",
            staff_id="worker",
            profile_id="default",
            profile_type="default",
            source_root="test",
        ),
        score=0.9,
    )


def test_exact_direct_search_uses_snapshot_before_qdrant() -> None:
    snapshot = _snapshot()
    core_calls: list[dict[str, Any]] = []

    class RecordingCore:
        def embed(self, query: str) -> list[float]:
            raise AssertionError("direct lookup must not call embedding")

        def match(self, **kwargs: Any) -> list[MatchResult]:
            core_calls.append(kwargs)
            return [_direct_match_result()]

    service = AgentSearchService(
        embedding_generator=RecordingCore(),
        vector_match_service=RecordingCore(),
        local_snapshot=snapshot,
    )

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="worker:default", top_k=3))
    )

    assert core_calls == []
    assert [result.profile_key for result in outcome.results] == [
        "worker:default"
    ]
    assert outcome.metadata["query_route"] == "direct_keyword"
    assert outcome.metadata["direct_snapshot_used"] is True
    assert outcome.metadata["direct_snapshot_exact"] is True
    assert outcome.metadata["status"] == "ok"
    assert outcome.metadata["reasons"] == []
    assert outcome.config_applied["direct_snapshot_used"] is True
    assert outcome.config_applied["direct_snapshot_exact"] is True
    assert outcome.config_applied["candidate_source"] == "direct_snapshot"
    assert outcome.config_applied["score_source"] == "direct_keyword"
    assert outcome.metadata["reasons"] == []




def test_exact_direct_recommendation_uses_snapshot_before_qdrant() -> None:
    class RecordingCore:
        def recommend(self, **kwargs: Any) -> Any:
            raise AssertionError("direct lookup must not call candidate service")

    service = AgentSearchService(
        candidate_recommendation_service=RecordingCore(),
        local_snapshot=_snapshot(),
    )
    command = AgentRecommendationCommand(
        question="worker:default",
        top_k=1,
        enable_rerank=False,
        reranker_model=None,
        expand_factor=1,
        vector_min_score=0.01,
        rerank_min_score=None,
        min_score_explicit=False,
    )

    outcome = asyncio.run(service.recommend(command))

    assert outcome.response.recommendations[0].worker_id == "worker"
    assert outcome.response.metadata["query_route"] == "direct_keyword"
    assert outcome.response.metadata["direct_snapshot_used"] is True
    assert outcome.response.metadata["candidate_source"] == "direct_snapshot"
    assert outcome.response.metadata["vector_search_used"] is False
    assert outcome.response.metadata["status"] == "ok"
    assert outcome.response.metadata["reasons"] == []


def test_exact_direct_search_does_not_consume_offload_executor() -> None:
    class RecordingCore:
        def match(self, **kwargs: Any) -> list[MatchResult]:
            raise AssertionError("exact snapshot hit must not enter Qdrant path")

    async def rejecting_run(operation: Any) -> Any:
        raise AssertionError("exact direct lookup must not consume a worker thread")

    service = AgentSearchService(
        embedding_generator=RecordingCore(),
        vector_match_service=RecordingCore(),
        local_snapshot=_snapshot(),
        offload=rejecting_run,
    )

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="worker:default", top_k=1))
    )

    assert outcome.results != ()
    assert outcome.metadata["status"] == "ok"


@pytest.mark.asyncio
async def test_direct_recommendation_route_reports_snapshot_source(monkeypatch: Any) -> None:
    from types import SimpleNamespace

    from app.adapters.http import recommend_routes
    from app.bootstrap import oss_business_routes
    from app.domain.models.bot_recommendation import BotRecommendationRequest

    class CandidateService:
        _vector_match_service = object()
        _embedding_generator = object()

        def recommend(self, **kwargs: Any) -> Any:
            raise AssertionError("exact direct lookup must not enter candidate service")

    monkeypatch.setattr(oss_business_routes, "require_oss_auth", lambda request: None)
    monkeypatch.setattr(
        recommend_routes, "get_candidate_recommendation_service", CandidateService
    )
    http_request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(local_retrieval_snapshot=_snapshot())),
    )
    request = BotRecommendationRequest(
        question="worker:default",
        topK=1,
        enable_rerank=False,
    )

    response = await recommend_routes.recommend_bots(http_request, request)

    assert response.metadata["query_route"] == "direct_keyword"
    assert response.metadata["direct_snapshot_used"] is True
    assert response.metadata["candidate_source"] == "direct_snapshot"
    assert response.metadata["vector_search_used"] is False


@pytest.mark.asyncio
async def test_direct_route_miss_does_not_report_snapshot_source(
    monkeypatch: Any,
) -> None:
    from types import SimpleNamespace

    from app.adapters.http import recommend_routes
    from app.bootstrap import oss_business_routes
    from app.domain.models.bot_recommendation import BotRecommendationRequest
    from app.domain.models.candidate_recommendation import (
        CandidateRecommendation,
        CandidateRecommendationResponse,
    )
    from app.domain.models.retrieval_mode import RetrievalMode

    class CandidateService:
        _vector_match_service = object()
        _embedding_generator = object()

        def recommend(self, **kwargs: Any) -> Any:
            return CandidateRecommendationResponse(
                question=kwargs["question"],
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                metadata={"vector_search_used": True},
                recommendations=[
                    CandidateRecommendation(
                        profile_key="semantic:worker:default",
                        worker_id="semantic-worker",
                        score=0.9,
                    )
                ],
                total_candidates=1,
                selected_candidates=1,
            )

    monkeypatch.setattr(oss_business_routes, "require_oss_auth", lambda request: None)
    monkeypatch.setattr(
        recommend_routes, "get_candidate_recommendation_service", CandidateService
    )
    http_request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(local_retrieval_snapshot=_snapshot())),
    )
    request = BotRecommendationRequest(
        question="missing:direct-worker",
        topK=1,
        enable_rerank=False,
    )

    response = await recommend_routes.recommend_bots(http_request, request)

    assert response.metadata["query_route"] == "direct_keyword"
    assert response.metadata.get("direct_snapshot_used") is not True
    assert response.metadata["candidate_source"] == "vector"
    assert response.metadata["vector_search_used"] is True
