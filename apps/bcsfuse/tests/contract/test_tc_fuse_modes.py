"""TC Fuse uses one current Card and shared retrieval for all legacy modes."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name

import pytest

from app.adapters.http.dependencies import fusion_dependencies as dependencies
from app.domain.models.llm_response import LLMResponse
from app.domain.models.retrieval_mode import RetrievalMode
from tests.contract.test_tc_catalog import runtime, sync  # noqa: F401
from tests.integration.test_g9_core_acceptance import EmptyGroupContext


class LocalFusionModel:
    def __init__(self):
        self.provider = self
        self.requests = []

    def generate(self, request, model=None):
        self.requests.append(request)
        schema = request.expected_schema_name
        if request.task_spec.task_type == "bot_profile_fuse":
            data = {
                "name": "Migration team",
                "description": "Database specialists",
                "persona": "Current expertise",
            }
        elif schema == "FusionRecommendation":
            data = {
                "summary": "Use staged schema changes",
                "decision": "conditional_yes",
                "confidence": 0.9,
            }
        elif schema == "ExpertPerspective":
            data = {
                "summary": "Use staged schema changes",
                "confidence": 0.9,
                "risk_level": "low",
                "rationale_summary": "Current database description",
            }
        else:
            data = {"summary": "Use staged schema changes", "confidence": 0.9}
        return LLMResponse(
            provider_id="local",
            model_id="local",
            raw_text="Use staged schema changes",
            structured_data=data if request.task_spec.need_structured_output else None,
            parse_success=True,
            latency_ms=1,
            finish_reason="stop",
        )


def test_fuse_recommendation_reuses_composed_tc_search(runtime):
    _client, registry = runtime
    assert dependencies.get_candidate_recommendation_service() is registry.require(
        "tc_search"
    )


def test_g5_retrieval_reads_current_participants_without_scanning(runtime, monkeypatch):
    client, registry = runtime
    sync(client)
    source = registry.require("worker_profile_source")

    def forbidden_scan():
        raise AssertionError("G5 must use shared search rather than scan all profiles")

    monkeypatch.setattr(source, "scan", forbidden_scan)
    response = dependencies.get_profile_retrieval_service().retrieve(
        "MySQL release",
        RetrievalMode.EXPERT_DIAGNOSIS,
        profile_keys=["bot:owner:legacy"],
        top_k=1,
        strict_participants=True,
    )
    assert len(response.results) == response.total_count == 1
    assert response.results[0].profile.profile_key == "bot:owner:legacy"
    assert "MySQL database migrations" in response.results[0].profile.searchable_text


def test_g5_skill_filter_runs_before_top_k(runtime):
    client, _registry = runtime
    sync(client, "first:owner", profile={"skill_sets": [{"name": "sql"}]})
    sync(client, "second:owner", profile={"skill_sets": [{"name": "python"}]})
    response = dependencies.get_profile_retrieval_service().retrieve(
        "Release scripts",
        RetrievalMode.EXPERT_DIAGNOSIS,
        profile_keys=["first:owner", "second:owner"],
        skill_filter=["python"],
        top_k=1,
        strict_participants=True,
    )
    assert [result.profile.staff_id for result in response.results] == ["second:owner"]


def test_availability_uses_current_state_for_worker_and_legacy_key(
    runtime, monkeypatch
):
    from app.infra.config.feature_flags import FeatureFlags

    client, _registry = runtime
    monkeypatch.setenv("ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING", "true")
    FeatureFlags.reset()
    sync(client)
    checker = dependencies._get_availability_checker()
    assert all(
        checker.check_availability(key).is_available
        for key in ("bot:owner", "bot:owner:legacy")
    )
    client.put("/v1/workers/bot:owner/offline").raise_for_status()
    assert all(
        not checker.check_availability(key).is_available
        for key in ("bot:owner", "bot:owner:legacy")
    )
    assert checker.check_availability("missing").unavailability_reason == "unregistered"


@pytest.mark.parametrize(
    "mode", ["agent", "conflict_alignment", "expert_diagnosis", "bot_profile_fuse"]
)
@pytest.mark.parametrize("legacy_key", [False, True])
def test_legacy_fuse_modes_use_tc_current_description(
    runtime, monkeypatch, mode, legacy_key
):
    client, _registry = runtime
    sync(client)
    client.put(
        "/v1/workers/bot:owner/config", json={"fusion_enable": True}
    ).raise_for_status()
    model = LocalFusionModel()
    monkeypatch.setattr(dependencies, "_get_llm_gateway_service", lambda: model)
    monkeypatch.setattr(dependencies, "_get_group_context_service", EmptyGroupContext)
    monkeypatch.setenv("ENABLE_REAL_LLM", "true")
    monkeypatch.setenv("ENABLE_G2_LLM_CONFLICT_ANALYSIS", "false")
    from app.application.services.bot_fuse import fusion_expert_chat_service

    monkeypatch.setattr(
        fusion_expert_chat_service, "get_server_ip", lambda: "127.0.0.1"
    )
    # G9 accepts Worker IDs directly; the other modes also accept legacy keys.
    participant = (
        "bot:owner:legacy" if legacy_key and mode != "bot_profile_fuse" else "bot:owner"
    )
    response = client.post(
        "/api/v1/groups/tc-group/fuse",
        json={
            "question": "How should we release MySQL schema changes?",
            "participants": [participant],
            "driver_bot_id": "bot:owner",
            "fusion_mode": mode,
            "options": {"timeout_ms": 10000, "strict_participants": True},
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["errors"] == []
    assert result["fusion_mode"] == mode
    assert result["recommendation"] is not None
    assert model.requests
    assert any(
        "MySQL database migrations" in request.user_prompt for request in model.requests
    )
    if mode == "bot_profile_fuse":
        assert result["extend_result"]["fused_profile"]["source_participants"] == [
            "bot:owner"
        ]
    else:
        assert result["perspectives"]
        assert all(p["status"] == "completed" for p in result["perspectives"])
        if mode == "expert_diagnosis":
            assert any(
                request.expected_schema_name == "ExpertPerspective"
                for request in model.requests
            )


@pytest.mark.parametrize(
    "api_type", ["openai", "openai-completions", "anthropic", "legacy-default"]
)
def test_external_model_api_selection_is_preserved(monkeypatch, api_type):
    from app.infra.llm.config.llm_settings import LLMSettings
    from app.infra.llm.providers.anthropic_compatible_provider import (
        AnthropicCompatibleProvider,
    )
    from app.infra.llm.providers.openai_compatible_provider import (
        OpenAICompatibleProvider,
    )

    monkeypatch.setenv("LLM_API_TYPE", api_type)
    provider = dependencies._create_llm_provider(
        LLMSettings(
            base_url="http://127.0.0.1:9",
            auth_token="test-token",
        )
    )
    expected = (
        OpenAICompatibleProvider
        if api_type.startswith("openai")
        else AnthropicCompatibleProvider
    )
    assert isinstance(provider, expected)
    provider.close()


@pytest.mark.parametrize("enabled_flag", ["ENABLE_REAL_LLM", "LLM_ENABLED"])
def test_model_gateway_respects_both_existing_enable_flags(monkeypatch, enabled_flag):
    import socket

    def forbidden_network(*args, **kwargs):
        raise AssertionError("Model composition must not contact a live service")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setenv("ENABLE_REAL_LLM", "false")
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-token")
    assert dependencies._get_llm_gateway_service() is None
    monkeypatch.setenv(enabled_flag, "true")
    assert dependencies._get_llm_gateway_service() is not None
