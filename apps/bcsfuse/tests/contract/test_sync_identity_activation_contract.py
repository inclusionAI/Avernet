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
from src.infra.public.stores.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
from src.interfaces.api import worker_profile_parity_routes as routes
from src.interfaces.api.worker_profile_routes import analysis, common, vector_sync


@pytest.fixture(params=["memory", "sqlite"])
def sync_client(monkeypatch, tmp_path, request):
    profile_store = (
        InMemoryWorkerProfileContentStore()
        if request.param == "memory"
        else SQLiteWorkerProfileContentStore(db_path=str(tmp_path / "profiles.sqlite"))
    )
    stores = {
        "worker_registry_store": InMemoryWorkerRegistryStore(),
        "worker_runtime_state_store": InMemoryWorkerRuntimeStateStore(),
        "worker_profile_content_store": profile_store,
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


def test_sparse_sync_preserves_existing_worker_and_profile_fields(sync_client):
    client, stores = sync_client
    url = "/v1/workers/test-bot:12345/sync"
    initial = payload(
        "default", True,
        description="Original description",
        responsibilities=["review"],
        domains=["engineering"],
        capabilities=[{"name": "review", "level": "expert"}],
        skills=[{"name": "code_review", "source": "builtin", "trust_level": "guarded"}],
        profile={
            "profile_id": "default", "display_name": "Original display",
            "soul_md": "Original soul",
            "contents": {"identity": "original", "profile": "Generated profile"},
            "skill_sets": [{"name": "code_review", "description": "Review code"}],
            "metadata": {"source": "bcs"},
        },
    )
    assert client.post(url, json=initial).status_code == 200

    response = client.post(url, json={"name": "Renamed Bot", "profile": {"profile_id": "default"}})

    assert response.status_code == 200, response.text
    worker = stores["worker_registry_store"].get_by_id("test-bot:12345")
    assert worker.identity.name == "Renamed Bot"
    assert worker.identity.description == "Original description"
    assert worker.responsibilities == ["review"]
    assert worker.domains == ["engineering"]
    assert [cap.name for cap in worker.capabilities] == ["review"]
    assert [skill.name for skill in worker.skills] == ["code_review"]
    assert worker.state.availability.value == "private"
    profile = stores["worker_profile_content_store"].get(worker.id, "default")
    assert profile.display_name == "Original display"
    assert profile.soul_md == "Original soul"
    assert profile.description == "Original description"
    assert profile.contents == {"identity": "original", "profile": "Generated profile"}
    assert [skill.name for skill in profile.skill_sets] == ["code_review"]
    assert profile.metadata == {"source": "bcs"}


def test_sync_updates_present_fields_and_merges_profile_maps(sync_client):
    client, stores = sync_client
    url = "/v1/workers/test-bot:12345/sync"
    assert client.post(url, json=payload(
        "default", True,
        capabilities=[{"name": "old", "level": "expert"}],
        skills=[{"name": "old_skill", "source": "builtin", "trust_level": "guarded"}],
        profile={
            "profile_id": "default", "soul_md": "Old soul",
            "contents": {"identity": "old", "profile": "Keep generated"},
            "skill_sets": [{"name": "old_skill"}],
            "metadata": {"owner": "alice"},
        },
    )).status_code == 200

    response = client.post(url, json={
        "name": "Named worker",
        "capabilities": [{"name": "new", "level": "novice"}],
        "skills": [{"name": "new_skill", "source": "builtin", "trust_level": "guarded"}],
        "trust_level": "trusted",
        "profile": {
            "profile_id": "default",
            "contents": {"identity": "new"},
            "skill_sets": [{"name": "new_skill"}],
            "metadata": {"revision": 2},
        },
    })

    assert response.status_code == 200, response.text
    worker = stores["worker_registry_store"].get_by_id("test-bot:12345")
    assert [cap.name for cap in worker.capabilities] == ["new"]
    assert [skill.name for skill in worker.skills] == ["new_skill"]
    assert worker.state.trust_level.value == "trusted"
    profile = stores["worker_profile_content_store"].get(worker.id, "default")
    assert profile.soul_md == "Old soul"
    assert profile.contents == {"identity": "new", "profile": "Keep generated"}
    assert [skill.name for skill in profile.skill_sets] == ["new_skill"]
    assert profile.metadata == {"owner": "alice", "revision": 2}


def test_explicit_empty_fields_clear_existing_worker_and_profile_data(sync_client):
    client, stores = sync_client
    url = "/v1/workers/test-bot:12345/sync"
    assert client.post(url, json=payload(
        "default", True,
        description="Old",
        responsibilities=["old"],
        domains=["old"],
        capabilities=[{"name": "old", "level": "expert"}],
        skills=[{"name": "old_skill", "source": "builtin", "trust_level": "guarded"}],
        profile={
            "profile_id": "default", "display_name": "Old", "soul_md": "Old",
            "contents": {"profile": "Old"},
            "skill_sets": [{"name": "old_skill"}],
            "metadata": {"source": "old"},
        },
    )).status_code == 200

    response = client.post(url, json={
        "name": "Named worker", "description": "", "responsibilities": [],
        "domains": [], "capabilities": [], "skills": [],
        "profile": {
            "profile_id": "default", "display_name": "", "soul_md": "",
            "contents": {}, "skill_sets": [], "metadata": {},
        },
    })

    assert response.status_code == 200, response.text
    worker = stores["worker_registry_store"].get_by_id("test-bot:12345")
    assert worker.identity.description == ""
    assert worker.responsibilities == []
    assert worker.domains == []
    assert worker.capabilities == []
    assert worker.skills == []
    profile = stores["worker_profile_content_store"].get(worker.id, "default")
    assert profile.display_name == ""
    assert profile.soul_md == ""
    assert profile.description == ""
    assert profile.contents == {}
    assert profile.skill_sets == []
    assert profile.metadata == {}


def test_profile_summary_is_preserved_until_explicitly_removed(sync_client):
    client, stores = sync_client
    url = "/v1/workers/test-bot:12345/sync"
    assert client.post(url, json=payload(
        "default", True,
        profile={
            "profile_id": "default",
            "contents": {"identity": "Assistant"},
            "summary": {"capability": "Review code"},
        },
    )).status_code == 200
    assert client.post(url, json={
        "name": "Named worker", "profile": {"profile_id": "default"},
    }).status_code == 200
    profile_store = stores["worker_profile_content_store"]
    assert profile_store.get("test-bot:12345", "default").contents == {
        "identity": "Assistant", "ecb_summary": {"capability": "Review code"},
        "profile": "名称: Named worker",
    }

    response = client.post(url, json={
        "name": "Named worker",
        "profile": {"profile_id": "default", "summary": None},
    })

    assert response.status_code == 200, response.text
    assert profile_store.get("test-bot:12345", "default").contents == {
        "identity": "Assistant", "profile": "名称: Named worker",
    }
