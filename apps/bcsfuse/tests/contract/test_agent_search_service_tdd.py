"""TDD contract for one application-level search/recommendation service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Protocol

import pytest

from app.application.agent_search_service import (
    AgentRecommendationCommand,
    degraded_reasons,
    AgentRecommendationOutcome,
    AgentSearchCommand,
    AgentSearchOutcome,
    AgentSearchService,
)
from app.domain.models.candidate_recommendation import CandidateRecommendationResponse
from app.domain.models.retrieval_mode import RetrievalMode


class EmbeddingGenerator(Protocol):
    def embed(self, query: str) -> list[float]: ...


class VectorMatchService(Protocol):
    def match(self, **kwargs: Any) -> list[Any]: ...


class CandidateRecommendationService(Protocol):
    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse: ...


@dataclass
class RecordingCore:
    embedding_calls: list[str]
    match_calls: list[dict[str, Any]]
    recommend_calls: list[dict[str, Any]]

    def embed(self, query: str) -> list[float]:
        self.embedding_calls.append(query)
        return [0.5, 0.5]

    def match(self, **kwargs: Any) -> list[Any]:
        self.match_calls.append(kwargs)
        return [{"profile_key": "bot:reader:default", "score": 0.9}]

    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        self.recommend_calls.append(kwargs)
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            metadata={"candidate_source": "vector"},
        )


def _service(
    core: RecordingCore,
    *,
    offload: Callable[[Callable[[], Any]], Any] | None = None,
) -> AgentSearchService:
    kwargs: dict[str, Any] = {}
    if offload is not None:
        kwargs["offload"] = offload
    return AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
        candidate_recommendation_service=core,
        **kwargs,
    )


def test_search_and_recommend_use_the_same_normalized_embedding_boundary() -> None:
    core = RecordingCore([], [], [])
    service = _service(core)
    raw_query = "  支付\n  风险\t评估  "

    search_outcome = asyncio.run(
        service.search(AgentSearchCommand(query=raw_query, top_k=3))
    )
    recommend_outcome = asyncio.run(
        service.recommend(
            AgentRecommendationCommand(
                question=raw_query,
                top_k=3,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=10,
                vector_min_score=0.01,
                rerank_min_score=None,
                min_score_explicit=False,
            )
        )
    )

    normalized = "支付 风险 评估"
    assert core.embedding_calls == [normalized, normalized]
    assert core.match_calls[0]["query"] == normalized
    assert core.recommend_calls[0]["question"] == normalized
    assert core.match_calls[0]["query_embedding"] == [0.5, 0.5]
    assert core.recommend_calls[0]["query_embedding"] == [0.5, 0.5]
    assert isinstance(search_outcome, AgentSearchOutcome)
    assert search_outcome.normalized_query == normalized
    assert recommend_outcome.response.question == normalized


def test_search_applies_shared_default_filters_and_fast_default() -> None:
    core = RecordingCore([], [], [])
    service = _service(core)

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="支付", top_k=5))
    )

    assert outcome.config_applied == {
        "reranker_model": None,
        "query_route": "semantic",
        "filters": {
            "availability": ["protected", "public"],
            "runtime_state": ["online"],
        },
    }
    assert core.match_calls[0]["filters"] == outcome.config_applied["filters"]
    assert core.match_calls[0]["top_k"] == 5


def test_search_embedding_failure_degrades_to_keyword_retrieval() -> None:
    class FailingEmbedding:
        def embed(self, query: str) -> list[float]:
            raise RuntimeError("embedding outage")

    core = RecordingCore([], [], [])
    service = AgentSearchService(
        embedding_generator=FailingEmbedding(),
        vector_match_service=core,
        candidate_recommendation_service=core,
    )

    outcome = asyncio.run(service.search(AgentSearchCommand(query="reader assistant", top_k=1)))

    assert core.match_calls[0]["query_embedding"] == []
    assert outcome.metadata["semantic_unavailable"] is True
    assert core.match_calls[0]["runtime_config"]["reranker_model"] is None


def test_embedding_rate_limit_degrades_through_literal_default() -> None:
    class RateLimitedEmbedding:
        def embed(self, query: str) -> list[float]:
            error = RuntimeError("embedding quota exceeded")
            error.status_code = 429
            raise error

    class LiteralDefaultMatch:
        def match(self, **kwargs: Any) -> list[Any]:
            kwargs["runtime_config"]["_retrieval"] = {
                "score_source": "regex_fallback",
                "semantic_unavailable": True,
                "keyword_unavailable": False,
                "literal_fallback_used": True,
                "literal_fallback_default_used": True,
            }
            return [{"profile_key": "bot:reader:default", "score": 0.01}]

    service = AgentSearchService(
        embedding_generator=RateLimitedEmbedding(),
        vector_match_service=LiteralDefaultMatch(),
        candidate_recommendation_service=None,
    )

    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="reader assistant", top_k=1))
    )

    assert outcome.metadata["semantic_unavailable"] is True
    assert outcome.metadata["semantic_status_code"] == 429
    assert outcome.metadata["literal_fallback_default_used"] is True
    assert len(outcome.results) == 1
    assert degraded_reasons(outcome.metadata) == (
        "semantic_unavailable",
        "embedding_quota_exhausted",
        "literal_fallback_used",
        "literal_fallback_default",
        "regex_fallback",
    )


def test_search_and_recommend_offload_blocking_work() -> None:
    async def scenario() -> AgentSearchOutcome:
        core = RecordingCore([], [], [])
        entered = asyncio.Event()
        released = asyncio.Event()

        async def offload(operation: Callable[[], Any]) -> Any:
            entered.set()
            await released.wait()
            return operation()

        service = _service(core, offload=offload)
        search_task = asyncio.create_task(
            service.search(AgentSearchCommand(query="支付", top_k=1))
        )
        await entered.wait()
        released.set()
        return await search_task

    outcome = asyncio.run(scenario())

    assert outcome.results[0]["profile_key"] == "bot:reader:default"


def test_blank_or_invalid_limits_are_rejected_before_core_calls() -> None:
    core = RecordingCore([], [], [])
    service = _service(core)

    with pytest.raises(ValueError):
        asyncio.run(service.search(AgentSearchCommand(query="   ", top_k=1)))
    with pytest.raises(ValueError):
        asyncio.run(service.search(AgentSearchCommand(query="x", top_k=0)))
    with pytest.raises(ValueError):
        asyncio.run(
            service.recommend(
                AgentRecommendationCommand(
                    question="   ",
                    top_k=1,
                    enable_rerank=False,
                    reranker_model=None,
                    expand_factor=10,
                    vector_min_score=0.01,
                    rerank_min_score=None,
                    min_score_explicit=False,
                )
            )
        )

    assert core.embedding_calls == []
    assert core.match_calls == []
    assert core.recommend_calls == []


def test_search_failure_returns_fallback_without_provider_details() -> None:
    class FailingMatch:
        match_calls: list[dict[str, Any]] = []

        def embed(self, query: str) -> list[float]:
            return [1.0]

        def match(self, **kwargs: Any) -> list[Any]:
            self.match_calls.append(kwargs)
            raise RuntimeError("private qdrant endpoint detail")

        def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
            raise AssertionError("recommend should not execute")

    service = AgentSearchService(
        embedding_generator=FailingMatch(),
        vector_match_service=FailingMatch(),
        candidate_recommendation_service=FailingMatch(),
    )

    outcome = asyncio.run(service.search(AgentSearchCommand(query="x", top_k=1)))

    assert "private qdrant endpoint detail" not in str(outcome.metadata)
    assert outcome.metadata["retrieval_unavailable"] is True
    assert outcome.metadata["fallback_unavailable"] is True
    assert FailingMatch.match_calls != []


def test_recommendation_candidate_budget_is_capped_at_three() -> None:
    """A large request factor cannot amplify the default recommendation chain."""

    core = RecordingCore([], [], [])
    service = _service(core)

    outcome = asyncio.run(
        service.recommend(
            AgentRecommendationCommand(
                question="支付",
                top_k=3,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=10,
                vector_min_score=0.01,
                rerank_min_score=0.6,
                min_score_explicit=False,
            )
        )
    )

    assert core.recommend_calls[0]["max_candidates"] == 3
    assert core.recommend_calls[0]["runtime_config"]["expand_factor"] == 3
    assert outcome.config_applied["expand_factor"] == 3


def test_recommendation_use_one_service_entrypoint_with_request_config() -> None:
    core = RecordingCore([], [], [])
    service = _service(core)

    outcome = asyncio.run(
        service.recommend(
            AgentRecommendationCommand(
                question="支付",
                top_k=8,
                enable_rerank=True,
                reranker_model="bge-reranker-v2-m3",
                expand_factor=10,
                vector_min_score=0.02,
                rerank_min_score=0.6,
                min_score_explicit=True,
                filters={"availability": ["protected"]},
            )
        )
    )

    call = core.recommend_calls[0]
    assert call["max_candidates"] == 8
    assert call["runtime_config"] == {
        "expand_factor": 3,
        "reranker_model": "bge-reranker-v2-m3",
        "query_route": "semantic",
    }
    assert call["filters"] == {
        "availability": ["protected"],
        "runtime_state": ["online"],
    }
    assert call["vector_min_score"] == 0.02
    assert call["rerank_min_score"] == 0.6
    assert outcome.elapsed_ms >= 0

class StartupProviderForRoutes:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None



def test_v1_search_route_delegates_to_agent_search_service(monkeypatch) -> None:
    """The v1 search transport projects one application-service outcome."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class AcceptingAuth:
        def validate_request(self, request: Any) -> bool:
            return True

    class ReadyVectorStore:
        connection = SimpleNamespace(mode="server")
        collection_name = "worker_profiles"
        index_owner = "pod-a"
        index_version = 1
        index_lag_seconds = 0
        index_stale = False
        read_only = False

        def check_ready(self) -> bool:
            return True

        @property
        def path(self) -> str:
            return "/tmp/agent-search-service-test"

    class RouteAgentSearchService:
        commands: list[AgentSearchCommand] = []

        def __init__(self, **kwargs: Any):
            self.constructor_kwargs = kwargs

        async def search(self, command: AgentSearchCommand) -> AgentSearchOutcome:
            self.__class__.commands.append(command)
            return AgentSearchOutcome(
                query=command.query,
                normalized_query=command.query.strip(),
                top_k=command.top_k,
                mode=command.mode,
                results=(
                    SimpleNamespace(
                        profile_key="bot:reader:default",
                        score=0.91,
                        aggregated_score=0.91,
                        fragment_matches=[],
                        metadata=SimpleNamespace(
                            payload={"short_profile": "reader"},
                            short_profile="reader",
                        ),
                    ),
                ),
                metadata={},
                config_applied={"reranker_model": None},
                elapsed_ms=1.5,
            )

    class Dependency:
        pass

    from app.bootstrap import oss_business_routes

    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_vector_match_service",
        lambda: Dependency(),
    )
    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_embedding_generator",
        lambda: Dependency(),
    )
    monkeypatch.setattr(
        oss_business_routes,
        "AgentSearchService",
        RouteAgentSearchService,
    )

    projection_calls: list[tuple[Any, AgentSearchOutcome, float]] = []

    def isolated_projection(
        request: Any, outcome: AgentSearchOutcome, *, elapsed_ms: float
    ) -> dict[str, Any]:
        projection_calls.append((request, outcome, elapsed_ms))
        return {"projection": "isolated"}

    monkeypatch.setattr(
        "app.adapters.http.search_response_projection.build_v1_search_response",
        isolated_projection,
    )

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProviderForRoutes())
    registry.register("auth", AcceptingAuth())
    registry.register("vector_store", ReadyVectorStore())
    app = create_bcsfuse_app(
        ApplicationContext(mode="runtime", startup_profile="opensource", registry=registry)
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/search",
            json={"query": " 支付 风险 ", "top_k": 1},
        )

    assert response.status_code == 200
    assert RouteAgentSearchService.commands[0].top_k == 1
    assert len(projection_calls) == 1
    projected_request, projected_outcome, elapsed_ms = projection_calls[0]
    assert projected_request.query == " 支付 风险 "
    assert projected_outcome is not None
    assert elapsed_ms >= 0
    assert response.json() == {"projection": "isolated"}


def test_v1_recommend_route_delegates_to_agent_search_service(monkeypatch) -> None:
    """The v1 recommendation transport projects the same service outcome."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.adapters.http import recommend_routes
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class AcceptingAuth:
        def validate_request(self, request: Any) -> bool:
            return True

    class ReadyVectorStore:
        connection = SimpleNamespace(mode="server")
        collection_name = "worker_profiles"
        index_owner = "pod-a"
        index_version = 1
        index_lag_seconds = 0
        index_stale = False
        read_only = False

        def check_ready(self) -> bool:
            return True

        @property
        def path(self) -> str:
            return "/tmp/agent-search-service-test"

    candidate_response = CandidateRecommendationResponse(
        question="支付风险",
        mode=RetrievalMode.EXPERT_DIAGNOSIS,
        metadata={"candidate_source": "vector"},
    )

    class RouteAgentSearchService:
        commands: list[AgentRecommendationCommand] = []

        def __init__(self, **kwargs: Any):
            self.constructor_kwargs = kwargs

        async def recommend(
            self, command: AgentRecommendationCommand
        ) -> AgentRecommendationOutcome:
            self.__class__.commands.append(command)
            return AgentRecommendationOutcome(
                question=command.question,
                response=candidate_response,
                config_applied={
                    "expand_factor": command.expand_factor,
                    "reranker_model": command.reranker_model,
                },
                elapsed_ms=1.5,
            )

    class CandidateService:
        _vector_match_service = None
        _embedding_generator = None

        def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
            raise AssertionError("v1 route called the candidate service directly")

    monkeypatch.setattr(
        recommend_routes,
        "get_candidate_recommendation_service",
        lambda: CandidateService(),
    )
    monkeypatch.setattr(
        recommend_routes,
        "AgentSearchService",
        RouteAgentSearchService,
    )
    monkeypatch.setattr(
        recommend_routes,
        "_rewrite_with_group_context",
        lambda **kwargs: kwargs["question"],
    )

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProviderForRoutes())
    registry.register("auth", AcceptingAuth())
    registry.register("vector_store", ReadyVectorStore())
    app = create_bcsfuse_app(
        ApplicationContext(mode="runtime", startup_profile="opensource", registry=registry)
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": " 支付 风险 ",
                "topK": 4,
                "enable_rerank": False,
                "expand_factor": 10,
            },
        )

    assert response.status_code == 200
    assert RouteAgentSearchService.commands[0].top_k == 4
    body = response.json()
    assert body["metadata"]["candidate_source"] == "vector"
    assert body["metadata"]["expand_factor"] == 10


def test_search_preserves_scalar_v1_filter_values() -> None:
    worker_id = "bot:owner"
    core = RecordingCore([], [], [])
    service = _service(core)

    asyncio.run(
        service.search(
            AgentSearchCommand(
                query=worker_id,
                top_k=1,
                filters={"worker_id": worker_id},
            )
        )
    )

    assert core.match_calls[0]["filters"]["worker_id"] == worker_id


def test_search_copies_delegate_diagnostics_into_outcome_metadata() -> None:
    """Vector-match degradation is visible to response-cache policy."""

    class DiagnosticCore(RecordingCore):
        def match(self, **kwargs: Any) -> list[Any]:
            kwargs["runtime_config"]["_retrieval"] = {
                "semantic_unavailable": True,
                "keyword_unavailable": False,
                "literal_fallback_used": True,
                "score_source": "regex_fallback",
            }
            return [{"profile_key": "bot:reader:default", "score": 0.9}]

    core = DiagnosticCore([], [], [])
    service = _service(core)
    outcome = asyncio.run(
        service.search(AgentSearchCommand(query="支付", top_k=1))
    )

    assert outcome.metadata["semantic_unavailable"] is True
    assert outcome.metadata["keyword_unavailable"] is False
    assert outcome.metadata["literal_fallback_used"] is True
    assert outcome.metadata["score_source"] == "regex_fallback"



def test_v1_search_projection_preserves_colon_worker_ids_and_bounded_evidence() -> None:
    from types import SimpleNamespace

    from app.adapters.http.search_response_projection import (
        build_v1_search_response,
    )
    from app.bootstrap.oss_business_routes import SearchRequestOSS

    fragments = [
        SimpleNamespace(
            fragment_type=f"fragment-{index}",
            score=0.1 + index,
            weighted_score=0.2 + index,
            content_preview=f"preview-{index}",
            content=f"content-{index}",
        )
        for index in range(7)
    ]
    result = SimpleNamespace(
        profile_key="team:bot:finance:default",
        score=0.91,
        aggregated_score=0.88,
        fragment_matches=fragments,
        metadata=SimpleNamespace(
            payload={"short_profile": "finance agent"}, short_profile="finance agent"
        ),
    )
    outcome = AgentSearchOutcome(
        query="支付 风险",
        normalized_query="支付 风险",
        top_k=3,
        mode="auto",
        results=(result,),
        metadata={},
        config_applied={"reranker_model": None},
        elapsed_ms=12.5,
    )
    response = build_v1_search_response(
        SearchRequestOSS(query="支付 风险", top_k=3),
        outcome,
        elapsed_ms=13.2,
    )

    assert response["query"] == "支付 风险"
    assert response["top_k"] == 3
    assert response["mode"] == "auto"
    assert response["results_count"] == 1
    assert response["timing_ms"] == 13.2
    assert response["config_applied"] == {"reranker_model": None}
    projected = response["results"][0]
    assert projected["profile_key"] == "team:bot:finance:default"
    assert projected["worker_id"] == "team:bot:finance"
    assert projected["profile_id"] == "default"
    assert projected["score"] == 0.91
    assert projected["aggregated_score"] == 0.88
    assert projected["short_profile"] == "finance agent"
    assert projected["source"] == "vector"
    assert len(projected["fragment_matches"]) == 5
    assert projected["fragment_matches"][0]["fragment_type"] == "fragment-0"
    assert projected["fragment_matches"][-1]["content"] == "content-4"


def test_search_and_recommend_record_stage_durations() -> None:
    """Core stage latency must be visible separately for capacity diagnosis."""

    from prometheus_client import REGISTRY

    def count(operation: str, stage: str) -> float:
        return float(
            REGISTRY.get_sample_value(
                "bcsfuse_search_stage_duration_seconds_count",
                {"operation": operation, "stage": stage},
            )
            or 0.0
        )

    before = {
        "embedding": count("search", "embedding"),
        "search_retrieval": count("search", "retrieval"),
        "recommend_retrieval": count("recommend", "retrieval"),
    }
    core = RecordingCore([], [], [])
    service = _service(core)

    asyncio.run(service.search(AgentSearchCommand(query="stage metric", top_k=1)))
    asyncio.run(
        service.recommend(
            AgentRecommendationCommand(
                question="stage recommend",
                top_k=1,
                enable_rerank=False,
                reranker_model=None,
                expand_factor=3,
                vector_min_score=0.01,
                rerank_min_score=None,
                min_score_explicit=False,
            )
        )
    )

    assert count("search", "embedding") - before["embedding"] == 1
    assert count("search", "retrieval") - before["search_retrieval"] == 1
    assert count("recommend", "retrieval") - before["recommend_retrieval"] == 1
