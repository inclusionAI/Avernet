"""Fusion must read profiles from the same provider as profile CRUD."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.bootstrap.app_factory import _configure_fusion_dependencies
from src.interfaces.api.dependencies import fusion_dependencies as dependencies
from src.infra.adapters import sqlite_worker_profile_content_store as sqlite_module


@pytest.fixture
def wiring(monkeypatch):
    monkeypatch.setattr(dependencies, "_app_context", None)
    monkeypatch.setattr(dependencies, "_api_profile_store", None)
    monkeypatch.setattr(dependencies, "_profile_merge_service", None)
    sqlite_factory = Mock(side_effect=AssertionError("Unexpected SQLite fallback"))
    monkeypatch.setattr(sqlite_module, "SQLiteWorkerProfileContentStore", sqlite_factory)
    return sqlite_factory


@pytest.mark.parametrize("has_local_cache", [False, True])
def test_profile_merge_reads_shared_provider(monkeypatch, wiring, has_local_cache):
    shared_store = Mock()
    profile = SimpleNamespace(worker_id="bot:owner")
    shared_store.get_active.return_value = profile
    monkeypatch.setattr(dependencies, "_app_context", SimpleNamespace(
        registry={"worker_profile_content_store": shared_store},
    ))
    if has_local_cache:
        monkeypatch.setattr(dependencies, "_api_profile_store", Mock())
    monkeypatch.setattr(dependencies, "_get_llm_gateway_service", lambda: Mock())
    monkeypatch.setattr(dependencies, "_get_fused_profile_storage_service", lambda: Mock())

    service = dependencies._get_profile_merge_service()
    assert service is not None
    assert service.collect_profiles(["bot:owner"]) == ([profile], [], [])
    shared_store.get_active.assert_called_once_with("bot:owner")
    wiring.assert_not_called()


def test_context_missing_provider_fails_without_local_fallback(monkeypatch, wiring):
    monkeypatch.setattr(dependencies, "_app_context", SimpleNamespace(registry={}))
    monkeypatch.setattr(dependencies, "_api_profile_store", Mock())
    with pytest.raises(RuntimeError, match="worker_profile_content_store"):
        dependencies._get_api_profile_store()
    wiring.assert_not_called()


def test_without_context_preserves_local_store(monkeypatch, wiring):
    local_store = Mock()
    monkeypatch.setattr(dependencies, "_api_profile_store", local_store)
    assert dependencies._get_api_profile_store() is local_store
    wiring.assert_not_called()


def test_fused_profile_storage_uses_composed_provider(monkeypatch):
    shared_store = Mock()
    monkeypatch.setattr(dependencies, "_fused_profile_storage_service", None)
    context = SimpleNamespace(
        registry={"fused_profile_store": shared_store},
    )

    _configure_fusion_dependencies(context)
    service = dependencies._fused_profile_storage_service

    assert service is not None
    assert service._repository is shared_store


def test_switching_composed_provider_invalidates_cached_fusion_services(monkeypatch):
    profile_store_a = Mock()
    profile_store_b = Mock()
    fused_store_a = Mock()
    fused_store_b = Mock()
    context_a = SimpleNamespace(
        registry={
            "worker_profile_content_store": profile_store_a,
            "fused_profile_store": fused_store_a,
        },
    )
    context_b = SimpleNamespace(
        registry={
            "worker_profile_content_store": profile_store_b,
            "fused_profile_store": fused_store_b,
        },
    )
    monkeypatch.setattr(dependencies, "_app_context", None)
    monkeypatch.setattr(dependencies, "_fused_profile_storage_service", None)
    monkeypatch.setattr(dependencies, "_profile_merge_service", None)
    monkeypatch.setattr(dependencies, "_fusion_expert_chat_service", None)
    monkeypatch.setattr(dependencies, "_group_fusion_service", None)
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
