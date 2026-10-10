"""
Worker API Contract Tests

验证 Worker API 端点符合 OpenAPI 契约。

Stage 1: 验证 API 端点存在并返回正确格式。

注意：Stage 1 API 使用扁平化请求格式（name/handle 在顶层），而非嵌套的 identity 结构。
"""

import pytest
from fastapi.testclient import TestClient


AUTH_HEADERS = {"Authorization": "Bearer test-token"}


@pytest.fixture
def composed_test_client(monkeypatch):
    """Build the public composition root used by A2/O3 contract parity."""
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    monkeypatch.delenv("BCSFUSE_EXPOSE_ADMIN", raising=False)

    from src.bootstrap.opensource_app import create_opensource_app

    app = create_opensource_app(mode="test")
    with TestClient(app) as client:
        yield client, app


def _worker_payload(worker_id: str) -> dict:
    return {
        "id": worker_id,
        "type": "bot",
        "name": "Contract Bot",
        "handle": f"@{worker_id}",
        "responsibilities": ["contract validation"],
        "capabilities": [{"name": "testing", "level": "expert"}],
        "availability": "public",
        "trust_level": "trusted",
    }


def test_composed_app_preserves_gateway_batch_config_query(
    composed_test_client,
) -> None:
    client, _ = composed_test_client
    worker_id = "wrk_gateway_batch_config"
    created = client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    )
    assert created.status_code == 201, created.text

    response = client.post(
        "/v1/workers/config/batch",
        headers=AUTH_HEADERS,
        json={"worker_ids": [worker_id, "wrk_missing"]},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "success": True,
        "data": {worker_id: {"fusion_enable": False}},
        "not_found_ids": ["wrk_missing"],
    }


class CanonicalOnlyProfileStore:
    """Profile provider exposing exactly the published typed port."""

    def __init__(self):
        from src.infra.public.stores.in_memory_worker_profile_content_store import (
            InMemoryWorkerProfileContentStore,
        )

        self._delegate = InMemoryWorkerProfileContentStore()

    def save(self, content):
        return self._delegate.save(content)

    def get(self, worker_id, profile_id):
        return self._delegate.get(worker_id, profile_id)

    def list_by_worker(self, worker_id):
        return self._delegate.list_by_worker(worker_id)

    def delete(self, worker_id, profile_id):
        return self._delegate.delete(worker_id, profile_id)

    def activate(self, worker_id, profile_id):
        return self._delegate.activate(worker_id, profile_id)

    def get_active(self, worker_id):
        return self._delegate.get_active(worker_id)

    def exists(self, worker_id, profile_id):
        return self._delegate.exists(worker_id, profile_id)

    def count(self, worker_id=None):
        return self._delegate.count(worker_id)

    def get_all_active(self):
        return self._delegate.get_all_active()


def test_composed_app_matches_worker_profile_lifecycle_contract(composed_test_client):
    """Freeze the Worker/Profile request corpus shared with the internal app."""
    client, app = composed_test_client
    worker_id = "wrk_composed_contract"
    profile_id = "release"

    created = client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    )
    assert created.status_code == 201, created.text
    assert {
        "id": created.json()["id"],
        "lifecycle_state": created.json()["lifecycle_state"],
        "runtime_state": created.json()["runtime_state"],
    } == {
        "id": worker_id,
        "lifecycle_state": "active",
        "runtime_state": "offline",
    }

    fetched = client.get(f"/v1/workers/{worker_id}", headers=AUTH_HEADERS)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == worker_id

    online = client.put(
        f"/v1/workers/{worker_id}/online",
        headers=AUTH_HEADERS,
    )
    assert online.status_code == 200, online.text
    assert online.json()["runtime_state"] == "online"
    assert online.json()["lifecycle_state"] == "active"

    profile = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={
            "display_name": "Release Validation",
            "soul_md": "# Release Validation",
        },
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["profile_id"] == profile_id

    activated = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True
    assert activated.json()["binding_updated"] is True
    assert activated.json()["worker_updated"] is True
    activated_worker = app.state.context.registry.get(
        "worker_registry_store"
    ).get_by_id(worker_id)
    assert activated_worker.active_profile_key == f"{worker_id}:{profile_id}"

    read_profile = client.get(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
    )
    assert read_profile.status_code == 200, read_profile.text
    assert read_profile.json()["display_name"] == "Release Validation"
    assert read_profile.json()["is_active"] is True

    offline = client.put(
        f"/v1/workers/{worker_id}/offline",
        headers=AUTH_HEADERS,
    )
    assert offline.status_code == 200, offline.text
    assert offline.json()["runtime_state"] == "offline"

    registry = app.state.context.registry
    assert registry.get("worker_registry_store").get_by_id(worker_id) is not None
    runtime_state = registry.get("worker_runtime_state_store").get_runtime_state(
        worker_id
    )
    assert getattr(runtime_state, "value", runtime_state) == "offline"
    binding = registry.get("worker_profile_binding_store").get_active_binding(
        worker_id
    )
    assert binding is not None
    assert binding.profile_key == f"{worker_id}:{profile_id}"

    deleted = client.delete(f"/v1/workers/{worker_id}", headers=AUTH_HEADERS)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {
        "success": True,
        "worker_id": worker_id,
        "deleted": True,
    }
    assert registry.get("worker_registry_store").get_by_id(worker_id) is None
    assert registry.get("worker_profile_content_store").get(
        worker_id, profile_id
    ) is None
    assert registry.get("worker_profile_binding_store").get_active_binding(
        worker_id
    ) is None

    deleted_again = client.delete(
        f"/v1/workers/{worker_id}",
        headers=AUTH_HEADERS,
    )
    assert deleted_again.status_code == 404, deleted_again.text
    assert deleted_again.json()["detail"]["code"] == "WORKER_NOT_FOUND"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/workers/wrk_missing_contract"),
        ("put", "/v1/workers/wrk_missing_contract/online"),
        ("put", "/v1/workers/wrk_missing_contract/offline"),
        ("put", "/api/v1/workers/wrk_missing_contract/online"),
        ("put", "/api/v1/workers/wrk_missing_contract/offline"),
        ("delete", "/v1/workers/wrk_missing_contract"),
    ],
)
def test_composed_app_returns_stable_missing_worker_error(
    composed_test_client,
    method,
    path,
):
    client, _app = composed_test_client

    response = getattr(client, method)(path, headers=AUTH_HEADERS)

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "WORKER_NOT_FOUND"


def test_composed_profile_routes_accept_canonical_only_provider(monkeypatch):
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    from src.bootstrap.app_factory import create_bcsfuse_app
    from src.bootstrap.application_context import build_application_context

    context = build_application_context(mode="test")
    canonical_store = CanonicalOnlyProfileStore()
    context.registry.register("worker_profile_content_store", canonical_store)
    app = create_bcsfuse_app(context)
    assert app.state.context.registry.get("worker_profile_content_store") is not canonical_store

    worker_id = "wrk_canonical_profile_provider"
    profile_id = "default"
    with TestClient(app) as client:
        assert client.post(
            "/v1/workers",
            headers=AUTH_HEADERS,
            json=_worker_payload(worker_id),
        ).status_code == 201

        upserted = client.put(
            f"/v1/workers/{worker_id}/profiles/{profile_id}",
            headers=AUTH_HEADERS,
            json={"display_name": "Canonical Provider", "soul_md": "# Canonical"},
        )
        activated = client.put(
            f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
            headers=AUTH_HEADERS,
        )
        updated = client.put(
            f"/v1/workers/{worker_id}/profiles/{profile_id}",
            headers=AUTH_HEADERS,
            json={"display_name": "Canonical Updated", "soul_md": "# Updated"},
        )
        fetched = client.get(
            f"/v1/workers/{worker_id}/profiles/{profile_id}",
            headers=AUTH_HEADERS,
        )

    assert upserted.status_code == 200, upserted.text
    assert activated.status_code == 200, activated.text
    assert activated.json()["binding_updated"] is True
    assert updated.status_code == 200, updated.text
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["display_name"] == "Canonical Updated"
    assert fetched.json()["is_active"] is True


def test_worker_delete_failure_preserves_durable_worker_and_profile(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_delete_failure_contract"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "Durable Profile", "soul_md": "# Durable"},
    ).status_code == 200

    registry = app.state.context.registry
    worker_store = registry.get("worker_registry_store")

    def fail_delete(_worker_id):
        raise RuntimeError("durable worker delete failed")

    monkeypatch.setattr(worker_store, "delete", fail_delete)
    response = client.delete(f"/v1/workers/{worker_id}", headers=AUTH_HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "DELETE_WORKER_ERROR"
    assert worker_store.get_by_id(worker_id) is not None
    profile_store = registry.get("worker_profile_content_store")
    assert profile_store.get(worker_id, profile_id) is not None


def test_dependent_cleanup_failure_keeps_worker_delete_retryable(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_dependent_cleanup_retry"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "Retryable Cleanup", "soul_md": "# Retry"},
    ).status_code == 200
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    ).status_code == 200

    registry = app.state.context.registry
    worker_store = registry.get("worker_registry_store")
    profile_store = registry.get("worker_profile_content_store")
    binding_store = registry.get("worker_profile_binding_store")
    original_delete = profile_store.delete
    delete_attempts = 0

    def fail_first_profile_delete(delete_worker_id, delete_profile_id):
        nonlocal delete_attempts
        delete_attempts += 1
        if delete_attempts == 1:
            raise RuntimeError("profile cleanup failed")
        return original_delete(delete_worker_id, delete_profile_id)

    monkeypatch.setattr(profile_store, "delete", fail_first_profile_delete)

    failed = client.delete(f"/v1/workers/{worker_id}", headers=AUTH_HEADERS)

    assert failed.status_code == 500
    assert failed.json()["detail"]["code"] == "DELETE_WORKER_ERROR"
    assert worker_store.get_by_id(worker_id) is not None
    assert profile_store.get(worker_id, profile_id) is not None
    assert binding_store.get_active_binding(worker_id) is not None

    retried = client.delete(f"/v1/workers/{worker_id}", headers=AUTH_HEADERS)

    assert retried.status_code == 200, retried.text
    assert worker_store.get_by_id(worker_id) is None
    assert profile_store.get(worker_id, profile_id) is None
    assert binding_store.get_active_binding(worker_id) is None


def test_profile_activation_propagates_binding_write_failure(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_binding_failure_contract"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "Binding Failure", "soul_md": "# Binding"},
    ).status_code == 200

    registry = app.state.context.registry
    binding_store = registry.get("worker_profile_binding_store")

    def fail_bind(**_kwargs):
        raise RuntimeError("binding persistence failed")

    monkeypatch.setattr(binding_store, "bind_profile", fail_bind)
    response = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "ACTIVATE_PROFILE_ERROR"
    assert registry.get("worker_profile_content_store").get_active(worker_id) is None


def test_profile_activation_failure_compensates_new_binding(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_activation_failure_contract"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "Activation Failure", "soul_md": "# Activation"},
    ).status_code == 200

    registry = app.state.context.registry
    profile_store = registry.get("worker_profile_content_store")
    monkeypatch.setattr(profile_store, "activate", lambda *_args: None)

    response = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "ACTIVATE_PROFILE_ERROR"
    assert (
        registry.get("worker_profile_binding_store").get_active_binding(worker_id)
        is None
    )
    assert registry.get("worker_registry_store").get_by_id(
        worker_id
    ).active_profile_key is None


def test_worker_update_failure_restores_previous_profile_activation(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_worker_update_failure"
    previous_profile_id = "previous"
    target_profile_id = "target"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    for profile_id in (previous_profile_id, target_profile_id):
        assert client.put(
            f"/v1/workers/{worker_id}/profiles/{profile_id}",
            headers=AUTH_HEADERS,
            json={"display_name": profile_id, "soul_md": f"# {profile_id}"},
        ).status_code == 200
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{previous_profile_id}/activate",
        headers=AUTH_HEADERS,
    ).status_code == 200

    registry = app.state.context.registry
    worker_store = registry.get("worker_registry_store")
    original_update = worker_store.update
    update_attempts = 0

    def fail_first_update(worker):
        nonlocal update_attempts
        update_attempts += 1
        if update_attempts == 1:
            raise RuntimeError("worker registry update failed")
        return original_update(worker)

    monkeypatch.setattr(worker_store, "update", fail_first_update)
    response = client.put(
        f"/v1/workers/{worker_id}/profiles/{target_profile_id}/activate",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "ACTIVATE_PROFILE_ERROR"
    active_profile = registry.get("worker_profile_content_store").get_active(
        worker_id
    )
    assert active_profile.profile_id == previous_profile_id
    active_binding = registry.get(
        "worker_profile_binding_store"
    ).get_active_binding(worker_id)
    assert active_binding.profile_key == f"{worker_id}:{previous_profile_id}"
    assert (
        worker_store.get_by_id(worker_id).active_profile_key
        == f"{worker_id}:{previous_profile_id}"
    )


def test_first_profile_activation_worker_update_failure_restores_inactive_state(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_first_activation_update_failure"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "First Activation", "soul_md": "# First"},
    ).status_code == 200

    registry = app.state.context.registry
    worker_store = registry.get("worker_registry_store")

    def fail_update(_worker):
        raise RuntimeError("worker registry update failed")

    monkeypatch.setattr(worker_store, "update", fail_update)
    response = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "ACTIVATE_PROFILE_ERROR"
    assert registry.get("worker_profile_content_store").get_active(worker_id) is None
    assert (
        registry.get("worker_profile_binding_store").get_active_binding(worker_id)
        is None
    )
    assert worker_store.get_by_id(worker_id).active_profile_key is None


def test_profile_activation_snapshot_failure_uses_stable_error_contract(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_activation_snapshot_failure"
    profile_id = "default"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201
    assert client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}",
        headers=AUTH_HEADERS,
        json={"display_name": "Snapshot Failure", "soul_md": "# Snapshot"},
    ).status_code == 200

    registry = app.state.context.registry
    profile_store = registry.get("worker_profile_content_store")

    def fail_active_snapshot(_worker_id):
        raise RuntimeError("profile provider unavailable")

    monkeypatch.setattr(profile_store, "get_active", fail_active_snapshot)
    response = client.put(
        f"/v1/workers/{worker_id}/profiles/{profile_id}/activate",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "ACTIVATE_PROFILE_ERROR"
    assert (
        registry.get("worker_profile_binding_store").get_active_binding(worker_id)
        is None
    )
    assert registry.get("worker_registry_store").get_by_id(
        worker_id
    ).active_profile_key is None


def test_runtime_registry_write_failure_rolls_back_and_returns_error(
    composed_test_client,
    monkeypatch,
):
    client, app = composed_test_client
    worker_id = "wrk_runtime_failure_contract"
    assert client.post(
        "/v1/workers",
        headers=AUTH_HEADERS,
        json=_worker_payload(worker_id),
    ).status_code == 201

    registry = app.state.context.registry
    worker_store = registry.get("worker_registry_store")

    def fail_update(_worker):
        raise RuntimeError("worker registry update failed")

    monkeypatch.setattr(worker_store, "update", fail_update)
    response = client.put(
        f"/v1/workers/{worker_id}/online",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "RUNTIME_STATE_UPDATE_ERROR"
    runtime_state = registry.get("worker_runtime_state_store").get_runtime_state(
        worker_id
    )
    assert getattr(runtime_state, "value", runtime_state) == "offline"
    assert worker_store.get_by_id(worker_id).state.runtime_state.value == "offline"
