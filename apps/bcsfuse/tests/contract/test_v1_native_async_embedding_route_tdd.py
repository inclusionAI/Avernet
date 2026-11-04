"""TDD for native online embedding in real v1 HTTP routes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
from app.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from app.domain.models.retrieval_mode import RetrievalMode


class _Config:
    def get(self, key: str, default: Any = None) -> Any:
        return default

    def get_int(self, key: str, default: int) -> int:
        return default

    def get_float(self, key: str, default: float) -> float:
        return default


class _Startup:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class _Auth:
    def validate_request(self, request: Any) -> bool:
        return True


class _VectorStore:
    connection = SimpleNamespace(mode="server")
    collection_name = "worker_profiles"
    index_owner = "route-pod"
    index_version = 1
    index_lag_seconds = 0
    index_stale = False
    read_only = False

    def check_ready(self) -> bool:
        return True

    @property
    def path(self) -> str:
        return "/tmp/native-async-route-test"


class _SyncFallback:
    def embed(self, text: str) -> list[float]:
        raise AssertionError("v1 online route must select native-async embedding")


class _NativeEmbedding:
    dimension = 2

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.5, 0.5]

    async def aclose(self) -> None:
        self.closed = True


class _VectorMatchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def match(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return [
            SimpleNamespace(
                profile_key="bot:reader:default",
                score=0.9,
                aggregated_score=0.9,
                fragment_matches=[],
                metadata=None,
                reasons=[],
            )
        ]


class _CandidateService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def recommend(self, **kwargs: Any) -> CandidateRecommendationResponse:
        self.calls.append(kwargs)
        return CandidateRecommendationResponse(
            question=kwargs["question"],
            mode=RetrievalMode.EXPERT_DIAGNOSIS,
            recommendations=[
                CandidateRecommendation(
                    profile_key="bot:reader:default",
                    worker_id="bot",
                    score=0.9,
                )
            ],
            total_candidates=1,
            selected_candidates=1,
        )


def _app(
    native: _NativeEmbedding,
) -> Any:
    from app.providers.public.async_model_shutdown_provider import (
        AsyncModelShutdownProvider,
    )

    registry = ProviderRegistry()
    registry.register("config", _Config())
    registry.register("secret_provider", object())
    registry.register(
        "startup_provider", AsyncModelShutdownProvider(_Startup(), provider=native)
    )
    registry.register("auth", _Auth())
    registry.register("vector_store", _VectorStore())
    registry.register("embedding_provider", _SyncFallback())
    registry.register("agent_online_embedding_provider", native)
    return create_bcsfuse_app(
        ApplicationContext(mode="runtime", startup_profile="opensource", registry=registry)
    )


def test_v1_search_route_uses_native_async_embedding_and_closes_it(
    monkeypatch,
) -> None:
    native = _NativeEmbedding()
    vector_match = _VectorMatchService()

    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_vector_match_service",
        lambda: vector_match,
    )
    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_embedding_generator",
        _SyncFallback,
    )

    with TestClient(_app(native)) as client:
        response = client.post(
            "/api/v1/search",
            json={"query": " Native async  route ", "top_k": 1},
        )

    assert response.status_code == 200, response.text
    assert native.calls == ["native async route"]
    assert vector_match.calls[0]["query_embedding"] == [0.5, 0.5]
    assert native.closed is True


def test_v1_recommend_route_uses_native_async_embedding_and_passes_vector(
    monkeypatch,
) -> None:
    native = _NativeEmbedding()
    candidates = _CandidateService()

    monkeypatch.setattr(
        "app.adapters.http.recommend_routes.get_candidate_recommendation_service",
        lambda: candidates,
    )
    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_embedding_generator",
        _SyncFallback,
    )
    monkeypatch.setattr(
        "app.adapters.http.recommend_routes._rewrite_with_group_context",
        lambda **kwargs: kwargs["question"],
    )

    with TestClient(_app(native)) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "question": " Native async  recommendation ",
                "topK": 1,
                "enable_rerank": False,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["recommendations"][0]["profile_key"] == "bot:reader:default"
    assert native.calls == ["native async recommendation"]
    assert candidates.calls[0]["query_embedding"] == [0.5, 0.5]
    assert native.closed is True
