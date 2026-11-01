"""TDD: composition owns resilience, budget, and snapshot dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
from app.adapters.http.dependencies import fusion_dependencies
from app.application.agent_search_service import (
    AgentRecommendationOutcome,
    AgentSearchOutcome,
)
from app.domain.models.candidate_recommendation import (
    CandidateRecommendationResponse,
)
from app.domain.models.retrieval_mode import RetrievalMode


class VectorStoreWithSnapshot:
    index_version = 9
    literal_fallback_index = object()


class CapturedSearchService:
    instances: list["CapturedSearchService"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        CapturedSearchService.instances.append(self)

    async def search(self, command: Any) -> AgentSearchOutcome:
        return AgentSearchOutcome(
            query=command.query,
            normalized_query=command.query,
            top_k=command.top_k,
            mode=command.mode,
            results=(),
            metadata={"status": "ok", "reasons": []},
            config_applied={"reranker_model": command.reranker_model},
            elapsed_ms=0.0,
        )


class CapturedRecommendationService:
    instances: list["CapturedRecommendationService"] = []
    _vector_match_service = None
    _embedding_generator = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        CapturedRecommendationService.instances.append(self)

    async def recommend(self, command: Any) -> AgentRecommendationOutcome:
        response = CandidateRecommendationResponse(
            question=command.question,
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            metadata={"status": "ok", "reasons": []},
        )
        return AgentRecommendationOutcome(
            question=command.question,
            response=response,
            config_applied={},
            elapsed_ms=0.0,
        )


def _context(vector_store: Any) -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", _StartupProvider())
    registry.register("vector_store", vector_store)
    return ApplicationContext(
        mode="test",
        startup_profile="opensource",
        registry=registry,
    )


@pytest.fixture(autouse=True)
def disable_background_index(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags.reset()
    yield
    FeatureFlags.reset()


class _StartupProvider:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class BoundedResilienceConfig:
    def get_bool(self, key: str, default: bool) -> bool:
        return False if key == "search.resilience.enabled" else default

    def get_int(self, key: str, default: int) -> int:
        if key != "search.resilience.max_entries":
            return default
        return 17

    def get_float(self, key: str, default: float) -> float:
        if key == "search.execution.embedding_timeout_seconds":
            return 0.31
        if key == "search.execution.retrieval_timeout_seconds":
            return 0.12
        if key == "search.execution.local_snapshot_timeout_seconds":
            return 0.04
        if key == "search.execution.total_timeout_seconds":
            return 0.77
        if "last_known_good" in key:
            return 120.0
        if "negative" in key:
            return 3.5
        return default


def test_resilience_configuration_is_composed_not_hardcoded(monkeypatch) -> None:
    from app.bootstrap.app_factory import create_bcsfuse_app

    context = _context(VectorStoreWithSnapshot())
    context.registry.register("config", BoundedResilienceConfig())
    app = create_bcsfuse_app(context)

    assert app.state.agent_search_resilience_cache is None
    assert app.state.agent_search_execution_budget.embedding_timeout_seconds == 0.31
    assert app.state.agent_search_execution_budget.retrieval_timeout_seconds == 0.12
    assert (
        app.state.agent_search_execution_budget.local_snapshot_timeout_seconds
        == 0.04
    )
    assert app.state.agent_search_execution_budget.total_timeout_seconds == 0.77


def test_composition_wires_resilience_budget_and_process_snapshot(monkeypatch) -> None:
    from app.bootstrap.app_factory import create_bcsfuse_app
    from tests.unit.bootstrap.test_app_factory import disable_background_index

    snapshot = VectorStoreWithSnapshot.literal_fallback_index
    app = create_bcsfuse_app(_context(VectorStoreWithSnapshot()))
    assert app.state.local_retrieval_snapshot is snapshot
    assert app.state.agent_search_resilience_cache is not None
    assert app.state.agent_search_execution_budget is not None
    assert app.state.agent_search_resilience_cache.current_index_version() == 9


def test_search_route_uses_composition_dependencies(monkeypatch) -> None:
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap import oss_business_routes
    from tests.unit.bootstrap.test_app_factory import disable_background_index

    CapturedSearchService.instances.clear()
    monkeypatch.setattr(oss_business_routes, "AgentSearchService", CapturedSearchService)
    monkeypatch.setattr(
        fusion_dependencies, "_get_vector_match_service", lambda: object()
    )
    monkeypatch.setattr(
        fusion_dependencies, "_get_embedding_generator", lambda: object()
    )
    monkeypatch.setattr(
        oss_business_routes, "require_oss_auth", lambda request: None
    )

    app = create_bcsfuse_app(_context(VectorStoreWithSnapshot()))
    response = TestClient(app).post(
        "/api/v1/search", json={"query": "python search", "top_k": 3}
    )

    assert response.status_code == 200
    kwargs = CapturedSearchService.instances[0].kwargs
    assert kwargs["result_cache"] is app.state.agent_search_result_cache
    assert kwargs["resilience_cache"] is app.state.agent_search_resilience_cache
    assert kwargs["execution_budget"] is app.state.agent_search_execution_budget
    assert kwargs["local_snapshot"] is app.state.local_retrieval_snapshot


def test_recommend_route_uses_composition_dependencies(monkeypatch) -> None:
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap import oss_business_routes
    from app.adapters.http import recommend_routes
    from tests.unit.bootstrap.test_app_factory import disable_background_index

    CapturedRecommendationService.instances.clear()
    monkeypatch.setattr(
        recommend_routes, "get_candidate_recommendation_service", lambda: object()
    )
    monkeypatch.setattr(
        recommend_routes, "AgentSearchService", CapturedRecommendationService
    )
    monkeypatch.setattr(
        oss_business_routes, "require_oss_auth", lambda request: None
    )

    app = create_bcsfuse_app(_context(VectorStoreWithSnapshot()))
    response = TestClient(app).post(
        "/api/v1/recommend",
        json={"question": "python search", "topK": 3, "enable_rerank": False},
    )

    assert response.status_code == 200
    kwargs = CapturedRecommendationService.instances[0].kwargs
    assert kwargs["result_cache"] is app.state.agent_search_result_cache
    assert kwargs["resilience_cache"] is app.state.agent_search_resilience_cache
    assert kwargs["execution_budget"] is app.state.agent_search_execution_budget
    assert kwargs["local_snapshot"] is app.state.local_retrieval_snapshot
