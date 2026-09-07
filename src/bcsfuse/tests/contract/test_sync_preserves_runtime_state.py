"""Both OSS sync routes preserve runtime state on visibility-only updates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.models.worker import Availability, TrustLevel, Worker, WorkerIdentity, WorkerState, WorkerType
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
from src.interfaces.api import worker_profile_parity_routes as routes


@pytest.fixture
def sync_app(monkeypatch):
    workers = InMemoryWorkerRegistryStore()
    runtime = InMemoryWorkerRuntimeStateStore()
    profiles = MagicMock()
    app = FastAPI()
    app.state.context = SimpleNamespace(registry={
        "worker_registry_store": workers,
        "worker_runtime_state_store": runtime,
        "worker_profile_content_store": profiles,
    })
    app.include_router(routes.api_router, prefix="/api/v1")
    app.include_router(routes.compat_router, prefix="/v1")
    monkeypatch.setattr(routes, "_require_auth", lambda request: None)
    monkeypatch.setattr(routes, "_sync_runtime_state_to_vector_store", MagicMock())
    availability_sync = MagicMock()
    monkeypatch.setattr(routes, "_sync_availability_to_vector_store", availability_sync)
    monkeypatch.setattr(routes, "_analyze_and_persist_async", AsyncMock())
    with TestClient(app) as client:
        yield client, workers, runtime, profiles, availability_sync


def seed_worker(workers, runtime, state):
    workers.create(Worker(
        id="bot:owner", type=WorkerType.BOT,
        responsibilities=[], capabilities=[],
        identity=WorkerIdentity(name="Bot", handle="@bot:owner"),
        state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.UNVERIFIED, runtime_state=state),
    ))
    runtime.set_runtime_state("bot:owner", state, updated_by="user-publication")


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
@pytest.mark.parametrize("state", [WorkerRuntimeState.OFFLINE, WorkerRuntimeState.ONLINE])
@pytest.mark.parametrize("availability", ["public", "protected", "private"])
def test_visibility_sync_preserves_runtime_and_still_updates_profile(sync_app, monkeypatch, prefix, state, availability):
    client, workers, runtime, profiles, availability_sync = sync_app
    seed_worker(workers, runtime, state)
    runtime_write = MagicMock(wraps=runtime.set_runtime_state)
    monkeypatch.setattr(runtime, "set_runtime_state", runtime_write)
    response = client.post(f"{prefix}/workers/bot:owner/sync", json={
        "name": "Updated Bot", "availability": availability,
        "profile": {"profile_id": "default", "contents": {"profile": "Updated profile"}},
    })
    assert response.status_code == 200, response.text
    assert response.json()["runtime_state"] == state.value
    runtime_write.assert_not_called()
    assert runtime.get_runtime_state("bot:owner") == state
    assert workers.get_by_id("bot:owner").state.runtime_state == state
    assert workers.get_by_id("bot:owner").state.availability.value == availability
    profiles.upsert_profile.assert_called_once()
    availability_sync.assert_called_with("bot:owner", availability)


@pytest.mark.parametrize("prefix", ["/v1", "/api/v1"])
@pytest.mark.parametrize("explicit", [None, "offline", "online"])
def test_new_worker_defaults_online_and_explicit_runtime_is_honored(sync_app, prefix, explicit):
    client, workers, runtime, _, _ = sync_app
    payload = {"name": "New Bot", "availability": "private", "profile": {"profile_id": "default"}}
    if explicit is not None:
        payload["runtime_state"] = explicit
    response = client.post(f"{prefix}/workers/bot:owner/sync", json=payload)
    assert response.status_code == 200, response.text
    expected = explicit or "online"
    assert response.json()["created"] is True
    assert response.json()["runtime_state"] == expected
    assert workers.get_by_id("bot:owner").state.runtime_state.value == expected
    # Sync's in-memory adapter retains the mapping supplied by the handler.
    assert runtime.get_runtime_state("bot:owner")["state"] == expected
    again = client.post(f"{prefix}/workers/bot:owner/sync", json={"name": "Still here", "profile": {"profile_id": "default"}})
    assert again.status_code == 200, again.text
    assert again.json()["runtime_state"] == expected


@pytest.mark.parametrize("explicit", ["offline", "online"])
def test_explicit_runtime_updates_existing_worker(sync_app, explicit):
    client, workers, runtime, _, _ = sync_app
    seed_worker(workers, runtime, WorkerRuntimeState.OFFLINE if explicit == "online" else WorkerRuntimeState.ONLINE)
    response = client.post("/v1/workers/bot:owner/sync", json={"name": "Bot", "runtime_state": explicit, "profile": {"profile_id": "default"}})
    assert response.status_code == 200, response.text
    assert response.json()["runtime_state"] == explicit
    assert workers.get_by_id("bot:owner").state.runtime_state.value == explicit


def test_null_runtime_with_missing_runtime_record_preserves_registry_state(sync_app):
    client, workers, runtime, _, _ = sync_app
    seed_worker(workers, runtime, WorkerRuntimeState.OFFLINE)
    runtime._states.clear()
    response = client.post("/v1/workers/bot:owner/sync", json={"name": "Bot", "runtime_state": None, "profile": {"profile_id": "default"}})
    assert response.status_code == 200, response.text
    assert response.json()["runtime_state"] == "offline"
    assert runtime.get_runtime_state("bot:owner") is None
    assert workers.get_by_id("bot:owner").state.runtime_state == WorkerRuntimeState.OFFLINE
