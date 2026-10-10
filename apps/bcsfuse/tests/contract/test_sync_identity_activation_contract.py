"""Sync must preserve draft isolation and the caller's Worker type."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
from src.infra.adapters.in_memory_worker_profile_binding_store import InMemoryWorkerProfileBindingStore
from src.infra.public.stores.in_memory_worker_profile_content_store import InMemoryWorkerProfileContentStore
from src.interfaces.api import worker_profile_parity_routes as routes
from src.interfaces.api.worker_profile_routes import analysis, common, vector_sync


@pytest.fixture
def sync_client(monkeypatch):
    stores = {
        "worker_registry_store": InMemoryWorkerRegistryStore(),
        "worker_runtime_state_store": InMemoryWorkerRuntimeStateStore(),
        "worker_profile_content_store": InMemoryWorkerProfileContentStore(),
        "worker_profile_binding_store": InMemoryWorkerProfileBindingStore(),
    }
    app = FastAPI()
    app.state.context = SimpleNamespace(registry=stores)
    app.include_router(routes.api_router, prefix="/api/v1")
    app.include_router(routes.compat_router, prefix="/v1")
    monkeypatch.setattr(common, "_require_auth", lambda request: None)
    monkeypatch.setattr(vector_sync, "_sync_runtime_state_to_vector_store", lambda *args: None)
    monkeypatch.setattr(vector_sync, "_sync_availability_to_vector_store", lambda *args: None)
    monkeypatch.setattr(analysis, "_analyze_and_persist_async", AsyncMock())
    monkeypatch.setattr("src.application.utils.drm_config_helper.is_capability_verify_enabled", lambda: False)
    with TestClient(app) as client:
        yield client, stores


def payload(profile_id="draft", activate=False, **fields):
    return {
        "name": "Named worker", "availability": "private", "runtime_state": "offline",
        "profile": {"profile_id": profile_id, "activate": activate}, **fields,
    }


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
@pytest.mark.parametrize("has_active", [False, True])
def test_draft_sync_does_not_change_any_active_profile(sync_client, prefix, has_active):
    client, stores = sync_client
    url = f"{prefix}/workers/test-bot:12345/sync"
    if has_active:
        assert client.post(url, json=payload("original", True)).status_code == 200
    response = client.post(url, json=payload())
    assert response.status_code == 200, response.text
    assert response.json()["profile_activated"] is False
    worker = stores["worker_registry_store"].get_by_id("test-bot:12345")
    binding = stores["worker_profile_binding_store"].get_active_binding(worker.id)
    active = stores["worker_profile_content_store"].get_active(worker.id)
    expected_key = "test-bot:12345:original" if has_active else None
    assert worker.active_profile_key == expected_key
    assert (binding.profile_key if binding else None) == expected_key
    assert (active.profile_id if active else None) == ("original" if has_active else None)
    assert stores["worker_profile_content_store"].get(worker.id, "draft") is not None


@pytest.mark.parametrize("worker_type", ["human", "bot"])
def test_sync_respects_worker_type(sync_client, worker_type):
    client, stores = sync_client
    response = client.post("/v1/workers/test-worker:12345/sync", json=payload(type=worker_type))
    assert response.status_code == 200, response.text
    assert stores["worker_registry_store"].get_by_id("test-worker:12345").type == worker_type


def test_unknown_worker_type_is_rejected_before_creating_worker(sync_client):
    client, stores = sync_client
    response = client.post("/v1/workers/test-worker:12345/sync", json=payload(type="unknown"))
    assert response.status_code == 422
    assert stores["worker_registry_store"].get_by_id("test-worker:12345") is None
