"""Batch lookup must supply BCN tags while preserving the existing list response."""

from types import SimpleNamespace

import pytest
from app.adapters.http.worker_profile_parity_routes import mgmt_router
from app.domain.models.worker import (
    Availability,
    TrustLevel,
    Worker,
    WorkerIdentity,
    WorkerState,
    WorkerType,
)
from app.domain.models.worker_runtime_state import WorkerRuntimeState
from app.infra.adapters.in_memory_worker_registry_store import (
    InMemoryWorkerRegistryStore,
)
from app.infra.public.auth.simple_token_auth_provider import SimpleTokenAuthProvider
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def batch_client(monkeypatch):
    monkeypatch.delenv("BCSFUSE_TRUST_GATEWAY", raising=False)
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    store = InMemoryWorkerRegistryStore()
    store.create(
        Worker(
            id="batch-test",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Batch test", handle="@batch-test"),
            responsibilities=["test"],
            domains=["test"],
            capabilities=[],
            state=WorkerState(
                runtime_state=WorkerRuntimeState.ONLINE,
                availability=Availability.PROTECTED,
                trust_level=TrustLevel.TRUSTED,
            ),
        )
    )
    app = FastAPI()
    app.state.context = SimpleNamespace(
        registry={
            "worker_registry_store": store,
            "auth": SimpleTokenAuthProvider(valid_token="contract-test"),
        }
    )
    app.include_router(mgmt_router, prefix="/v1")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer contract-test"
        yield client


def test_batch_supplies_client_map_and_preserves_legacy_list(batch_client):
    response = batch_client.post(
        "/v1/workers/batch",
        json={
            "worker_ids": ["batch-test", "missing", "batch-test"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {
        "batch-test": {
            "name": "Batch test",
            "runtime_state": "online",
            "availability": "protected",
            "profile_tags": {"trust_level": "trusted"},
        }
    }
    assert [worker["id"] for worker in body["workers"]] == ["batch-test", "batch-test"]
    assert body["total"] == 2
    assert body["not_found_ids"] == ["missing"]


def test_batch_all_missing_is_success_with_empty_map(batch_client):
    response = batch_client.post("/v1/workers/batch", json={"worker_ids": ["missing"]})
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {},
        "workers": [],
        "not_found_ids": ["missing"],
        "total": 0,
    }


def test_batch_still_requires_authentication(batch_client):
    del batch_client.headers["Authorization"]
    response = batch_client.post(
        "/v1/workers/batch", json={"worker_ids": ["batch-test"]}
    )
    assert response.status_code == 401
