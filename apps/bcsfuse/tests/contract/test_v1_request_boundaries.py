"""V1 search and recommendation requests must be bounded before execution."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry


class Config:
    def get(self, key, default=None):
        return default

    def get_int(self, key, default: int = 0) -> int:
        return default

    def get_float(self, key, default: float = 0.0) -> float:
        return default


class StartupProvider:
    async def initialize(self):
        return None

    async def shutdown(self):
        return None


class AuthProvider:
    def validate_request(self, request):
        return True


class ReadyVectorStore:
    connection = type("Connection", (), {"mode": "server"})()
    collection_name = "worker_profiles"
    index_owner = "pod-a"
    index_version = 1
    index_lag_seconds = 0.0
    index_stale = False
    read_only = False

    def check_ready(self):
        return True


def build_app() -> object:
    registry = ProviderRegistry()
    registry.register("config", Config())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", ReadyVectorStore())
    registry.register("auth", AuthProvider())
    return create_bcsfuse_app(
        ApplicationContext(mode="runtime", startup_profile="opensource", registry=registry)
    )


def _reject_execution(monkeypatch):
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags.reset()
    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_vector_match_service",
        lambda: (_ for _ in ()).throw(
            AssertionError("invalid search request reached core execution")
        ),
    )
    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_embedding_generator",
        lambda: (_ for _ in ()).throw(
            AssertionError("invalid search request reached embedding execution")
        ),
    )
    monkeypatch.setattr(
        "app.adapters.http.recommend_routes.get_candidate_recommendation_service",
        lambda: (_ for _ in ()).throw(
            AssertionError("invalid recommendation request reached core execution")
        ),
    )


def test_v1_search_request_boundaries_reject_before_execution(monkeypatch):
    cases = [
        ("/api/v1/search", {"query": "x" * 4097, "top_k": 1}),
        (
            "/api/v1/search",
            {
                "query": "支付",
                "top_k": 1,
                "filters": {f"tag{i}": ["value"] for i in range(17)},
            },
        ),
        (
            "/api/v1/search",
            {
                "query": "支付",
                "top_k": 1,
                "filters": {"domains": [f"value{i}" for i in range(33)]},
            },
        ),
        (
            "/api/v1/search",
            {"query": "支付", "top_k": 1, "filters": {"tags": ["value"]}},
        ),
        (
            "/api/v1/search",
            {"query": "支付", "top_k": 1, "filters": {"domains": [123]}},
        ),
        (
            "/api/v1/search",
            {"query": "支付", "top_k": 1, "filters": {"domains": ["x" * 257]}},
        ),
        ("/api/v1/recommend", {"question": "x" * 4097, "topK": 1}),
        (
            "/api/v1/recommend",
            {"question": "支付", "topK": 1, "filters": {f"tag{i}": ["value"] for i in range(17)}},
        ),
        (
            "/api/v1/recommend",
            {"question": "支付", "topK": 1, "filters": {"domains": [f"value{i}" for i in range(33)]}},
        ),
        (
            "/api/v1/recommend",
            {"question": "支付", "topK": 1, "filters": {"tags": ["value"]}},
        ),
        (
            "/api/v1/recommend",
            {"question": "支付", "topK": 1, "filters": {"domains": [123]}},
        ),
        (
            "/api/v1/recommend",
            {"question": "支付", "topK": 1, "filters": {"domains": ["x" * 257]}},
        ),
    ]

    _reject_execution(monkeypatch)
    app = build_app()
    with TestClient(app) as client:
        for path, payload in cases:
            response = client.post(path, json=payload)
            assert response.status_code == 422, (path, response.text)
            assert response.json()["detail"] is not None


def test_v1_filter_contract_preserves_exact_worker_string_filters():
    from app.bootstrap.oss_business_routes import SearchRequestOSS
    from app.domain.models.bot_recommendation import BotRecommendationRequest

    search_request = SearchRequestOSS(
        query="worker-1",
        filters={"worker_id": "acceptance_worker:1"},
    )
    recommend_request = BotRecommendationRequest(
        question="worker-1",
        filters={"worker_id": "acceptance_worker:1"},
    )

    assert search_request.filters == recommend_request.filters == {
        "worker_id": "acceptance_worker:1"
    }


def test_v1_oversized_body_is_rejected_before_admission_and_execution(monkeypatch):
    _reject_execution(monkeypatch)
    payload = json.dumps({"query": "x" * 65_540}).encode()
    app = build_app()

    too_large_before = REGISTRY.get_sample_value(
        "bcsfuse_search_request_too_large_total",
        {"operation": "search", "api_version": "v1"},
    )
    too_large_before = float(too_large_before or 0)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/search",
            content=payload,
            headers={"content-type": "application/json"},
        )
        metrics_text = client.get("/metrics").text

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "SEARCH_REQUEST_TOO_LARGE"
    too_large_after = REGISTRY.get_sample_value(
        "bcsfuse_search_request_too_large_total",
        {"operation": "search", "api_version": "v1"},
    )
    assert float(too_large_after or 0) >= too_large_before + 1
    assert "bcsfuse_search_request_too_large_total" in metrics_text


def test_v1_oversized_chunked_body_is_rejected_without_content_length():
    from app.bootstrap.search_capacity import (
        SearchCapacityMiddleware,
        SearchCapacitySettings,
    )

    async def application(scope, receive, send):
        while (await receive())["type"] == "http.request":
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/search",
        "headers": [],
        "query_string": b"",
    }
    body_messages = [
        {"type": "http.request", "body": b"x" * 33_000, "more_body": True},
        {"type": "http.request", "body": b"x" * 33_000, "more_body": False},
    ]
    sent = []

    async def receive():
        if body_messages:
            return body_messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    middleware = SearchCapacityMiddleware(
        application,
        settings=SearchCapacitySettings(
            max_inflight=1, retry_after_seconds=1.0
        ),
    )
    asyncio.run(middleware(scope, receive, send))

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert any(b'"SEARCH_REQUEST_TOO_LARGE"' in message.get("body", b"") for message in sent)


def test_v1_search_does_not_log_query_text_or_filter_values(monkeypatch, caplog):
    import logging

    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags.reset()
    monkeypatch.setattr(
        "app.adapters.http.dependencies.fusion_dependencies._get_vector_match_service",
        lambda: (_ for _ in ()).throw(AssertionError("controlled core failure")),
    )

    app = build_app()
    with TestClient(app) as client, caplog.at_level(
        logging.INFO, logger="app.bootstrap.oss_business_routes"
    ):
        response = client.post(
            "/api/v1/search",
            json={"query": "secret-query-text", "filters": {"domains": ["secret-filter-value"]}},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "fallback_unavailable"
    assert "secret-query-text" not in caplog.text
    assert "secret-filter-value" not in caplog.text


class UnavailableRecommendationService:
    _vector_match_service = object()
    _embedding_generator = object()

    def recommend(self, **kwargs):
        raise RuntimeError("private reranker endpoint failure detail")


def test_v1_dependency_failures_use_stable_503_without_provider_details(monkeypatch):
    from app.infra.config.feature_flags import FeatureFlags

    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags.reset()

    app = build_app()
    with TestClient(app) as client:
        monkeypatch.setattr(
            "app.adapters.http.dependencies.fusion_dependencies._get_vector_match_service",
            lambda: (_ for _ in ()).throw(
                RuntimeError("private qdrant server failure detail")
            ),
        )
        search_response = client.post(
            "/api/v1/search", json={"query": "支付", "top_k": 1}
        )

        service = UnavailableRecommendationService()
        monkeypatch.setattr(
            "app.adapters.http.recommend_routes.get_candidate_recommendation_service",
            lambda: service,
        )
        recommend_response = client.post(
            "/api/v1/recommend",
            json={"question": "支付", "topK": 1, "enable_rerank": False},
        )

    assert search_response.status_code == 200
    assert search_response.json()["status"] == "fallback_unavailable"
    assert "private qdrant server failure detail" not in search_response.text

    assert recommend_response.status_code == 200
    recommend_metadata = recommend_response.json()["metadata"]
    assert recommend_metadata["status"] == "fallback_unavailable"
    assert "private reranker endpoint failure detail" not in recommend_response.text


def test_search_delivery_source_never_logs_raw_query_text():
    """Every mounted or optional search transport obeys the privacy contract."""
    import ast
    from pathlib import Path

    def is_query_expression(node: object) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "query"
            and isinstance(node.value, ast.Name)
            and node.value.id in {"request", "search_req"}
        )

    def contains_query_text(node: object) -> bool:
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ("request.query" in node.value or "search_req.query" in node.value)
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and len(node.args) == 1
            and is_query_expression(node.args[0])
        ):
            return False
        if is_query_expression(node):
            return True
        for child in ast.iter_child_nodes(node):
            if contains_query_text(child):
                return True
        return False

    source_files = [
        Path("src/app/adapters/http/profile_routes.py"),
        Path("src/app/adapters/http/oss_routes.py"),
        Path("src/app/bootstrap/oss_business_routes.py"),
    ]
    offenders: list[str] = []
    for source_file in source_files:
        tree = ast.parse(source_file.read_text(), filename=str(source_file))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            value = node.func.value
            if not (isinstance(value, ast.Name) and value.id == "logger"):
                continue
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            if any(contains_query_text(argument) for argument in arguments):
                offenders.append(f"{source_file}:{node.lineno}")
                break

    assert offenders == []
