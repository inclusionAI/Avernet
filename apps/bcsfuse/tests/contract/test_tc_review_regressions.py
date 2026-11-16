"""Review-found boundary regressions: identities, raw scores and Qdrant settings."""
# ruff: noqa: F811 -- imported pytest fixtures are injected by name

from types import SimpleNamespace

import pytest
from search_core.plugin_api.reranker import RerankResult

from tests.contract.test_tc_catalog import runtime, sync  # noqa: F401


def test_structured_profile_lookup_cannot_read_a_different_workers_exact_id(runtime):
    client, registry = runtime
    sync(
        client,
        "alpha",
        description="Alpha migration",
        profile={"profile_id": "default"},
    )
    sync(
        client,
        "alpha:default",
        description="Other private content",
        availability="private",
        runtime_state="offline",
    )
    source = registry.require("worker_profile_source")
    assert source.get_profile("alpha", "default").staff_id == "alpha"
    assert source.get_profile("alpha", "default").searchable_text == "Alpha migration"
    assert source.get_by_participant("alpha:default").staff_id == "alpha:default"
    assert source.get_by_participant("alpha:default:legacy").staff_id == "alpha:default"


@pytest.mark.parametrize("score", [-2.5, 2.5])
def test_recommend_preserves_finite_model_scores_without_legacy_range_scaling(
    runtime, monkeypatch, score
):
    client, registry = runtime
    sync(client)

    def rerank(query, candidates, top_k=5):
        return [
            RerankResult(candidate["id"], score) for candidate in candidates[:top_k]
        ]

    monkeypatch.setattr(registry.require("reranker_provider"), "rerank", rerank)
    response = client.post(
        "/api/v1/recommend", json={"question": "MySQL", "min_score": 0.99}
    )
    assert response.status_code == 200, response.text
    assert response.json()["recommendations"][0]["score"] == score


def test_storage_keeps_existing_qdrant_server_credentials_and_transport_settings(
    runtime, monkeypatch
):
    from app.tc import providers

    _client, registry = runtime
    registry.unregister("tc_catalog_repository")
    registry.unregister("tc_index")
    captured = []
    monkeypatch.setattr(
        providers,
        "QdrantRetrievalIndex",
        lambda **kwargs: captured.append(kwargs) or object(),
    )
    for key, value in {
        "QDRANT_MODE": "server",
        "QDRANT_URL": "http://127.0.0.1:6333",
        "QDRANT_API_KEY": "test-key",
        "QDRANT_PREFER_GRPC": "false",
        "QDRANT_GRPC_PORT": "16334",
        "QDRANT_TIMEOUT_SECONDS": "37",
    }.items():
        monkeypatch.setenv(key, value)
    providers.storage(
        SimpleNamespace(
            registry=registry, config=registry.require("config"), mode="dev_smoke"
        )
    )
    connection = captured[0]["connection"]
    assert connection.mode == "server"
    assert connection.api_key == "test-key"
    assert not connection.prefer_grpc
    assert connection.grpc_port == 16334
    assert connection.timeout == 37


def test_g1_uses_the_original_participant_identity_before_any_colon_split(
    runtime, monkeypatch
):
    from app.adapters.http.dependencies import fusion_dependencies as dependencies
    from app.infra.config.feature_flags import FeatureFlags
    from tests.contract.test_tc_fuse_modes import LocalFusionModel
    from tests.integration.test_g9_core_acceptance import EmptyGroupContext

    client, _registry = runtime
    sync(
        client,
        "alpha",
        description="Alpha migration",
        profile={"profile_id": "default"},
    )
    sync(
        client,
        "alpha:default",
        description="Other private content",
        availability="private",
        runtime_state="offline",
    )
    model = LocalFusionModel()
    monkeypatch.setattr(dependencies, "_get_llm_gateway_service", lambda: model)
    monkeypatch.setattr(dependencies, "_get_group_context_service", EmptyGroupContext)
    monkeypatch.setenv("ENABLE_REAL_LLM", "true")
    monkeypatch.setenv("ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING", "false")
    FeatureFlags.reset()
    for participant, expected, other in (
        ("alpha", "Alpha migration", "Other private content"),
        ("alpha:default", "Other private content", "Alpha migration"),
    ):
        model.requests.clear()
        response = client.post(
            "/api/v1/groups/identity/fuse",
            json={
                "question": "Migration review",
                "participants": [participant],
                "fusion_mode": "agent",
                "options": {"strict_participants": True},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["errors"] == []
        assert any(expected in request.user_prompt for request in model.requests)
        assert all(other not in request.user_prompt for request in model.requests)


def test_disabled_model_fuse_reads_current_description_without_old_stores(
    runtime, monkeypatch
):
    client, _registry = runtime
    sync(client)
    monkeypatch.setenv("ENABLE_REAL_LLM", "false")
    monkeypatch.setenv("LLM_ENABLED", "false")
    response = client.post(
        "/api/v1/groups/fallback/fuse",
        json={
            "question": "MySQL migration",
            "participants": ["bot:owner:legacy"],
            "fusion_mode": "agent",
            "options": {"strict_participants": True},
        },
    )
    assert response.status_code == 200, response.text
    assert (
        "MySQL database migrations" in response.json()["perspectives"][0]["perspective"]
    )
    assert response.json()["metadata"]["llm_used"] is False
