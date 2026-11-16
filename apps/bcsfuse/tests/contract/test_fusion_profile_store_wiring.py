"""Fusion must use the composed TC profile and shared fusion-history stores."""

from unittest.mock import Mock

import pytest

from app.adapters.http.dependencies import fusion_dependencies as dependencies
from app.bootstrap.app_factory import _configure_fusion_dependencies
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
from tests.contract import test_tc_catalog

tc_runtime = test_tc_catalog.runtime


def context_with(**providers):
    registry = ProviderRegistry()
    for name, provider in providers.items():
        registry.register(name, provider)
    return ApplicationContext(
        mode="test", startup_profile="injected-host", registry=registry
    )


@pytest.mark.parametrize("reset_services", [False, True])
def test_profile_merge_reads_shared_tc_provider(
    tc_runtime, monkeypatch, reset_services
):
    client, registry = tc_runtime
    test_tc_catalog.sync(client)
    if reset_services:
        dependencies.reset_fusion_services()
        _configure_fusion_dependencies(client.app.state.context)
    monkeypatch.setattr(dependencies, "_get_llm_gateway_service", lambda: Mock())

    service = dependencies._get_profile_merge_service()
    profiles, warnings, errors = service.collect_profiles(["bot:owner"])

    assert service._store is registry.require("worker_profile_content_store")
    assert service._storage_service._repository is registry.require(
        "fused_profile_store"
    )
    assert warnings == errors == []
    assert [profile.worker_id for profile in profiles] == ["bot:owner"]
    assert profiles[0].description == "MySQL database migrations"


def test_context_missing_provider_fails_without_local_fallback(monkeypatch):
    monkeypatch.setattr(dependencies, "_app_context", context_with())
    with pytest.raises(LookupError, match="worker_profile_content_store"):
        dependencies._get_api_profile_store()


def test_without_context_requires_explicit_profile_provider(monkeypatch):
    monkeypatch.setattr(dependencies, "_app_context", None)
    with pytest.raises(RuntimeError, match="composed.*worker_profile_content_store"):
        dependencies._get_api_profile_store()


def test_fused_profile_storage_uses_composed_provider():
    shared_store = Mock()
    context = context_with(fused_profile_store=shared_store)

    _configure_fusion_dependencies(context)
    service = dependencies._get_fused_profile_storage_service()

    assert service._repository is shared_store


def test_switching_composed_provider_invalidates_cached_fusion_services(monkeypatch):
    profile_store_a, profile_store_b = Mock(), Mock()
    fused_store_a, fused_store_b = Mock(), Mock()
    context_a = context_with(
        worker_profile_content_store=profile_store_a,
        fused_profile_store=fused_store_a,
    )
    context_b = context_with(
        worker_profile_content_store=profile_store_b,
        fused_profile_store=fused_store_b,
    )
    monkeypatch.setattr(dependencies, "_get_llm_gateway_service", lambda: Mock())

    _configure_fusion_dependencies(context_a)
    profile_merge_a = dependencies._get_profile_merge_service()
    fusion_chat_a = dependencies._get_fusion_expert_chat_service()
    dependencies._group_fusion_service = object()

    _configure_fusion_dependencies(context_b)
    profile_merge_b = dependencies._get_profile_merge_service()
    fusion_chat_b = dependencies._get_fusion_expert_chat_service()

    assert profile_merge_b is not profile_merge_a
    assert profile_merge_b._store is profile_store_b
    assert profile_merge_b._storage_service._repository is fused_store_b
    assert fusion_chat_b is not fusion_chat_a
    assert fusion_chat_b._storage_service._repository is fused_store_b
    assert dependencies._group_fusion_service is None
