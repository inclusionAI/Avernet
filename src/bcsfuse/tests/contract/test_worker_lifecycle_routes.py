"""BCS-facing worker lifecycle routes exposed by the OSS application."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.bootstrap import oss_worker_lifecycle_routes as lifecycle_routes
from src.bootstrap.opensource_app import create_opensource_app
from src.domain.models.worker import (
    Availability,
    TrustLevel,
    Worker,
    WorkerIdentity,
    WorkerState,
    WorkerType,
)


def test_authenticated_product_route_deletes_worker_without_admin_routes(monkeypatch):
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    monkeypatch.delenv("BCSFUSE_EXPOSE_ADMIN", raising=False)
    app = create_opensource_app(mode="test")
    workers = app.state.context.registry.get("worker_registry_store")
    workers.create(
        Worker(
            id="bot:owner",
            type=WorkerType.BOT,
            responsibilities=[],
            capabilities=[],
            identity=WorkerIdentity(name="Bot", handle="@bot:owner"),
            state=WorkerState(
                availability=Availability.PROTECTED,
                trust_level=TrustLevel.UNVERIFIED,
            ),
        )
    )
    events = []
    original_delete = workers.delete

    class ProfileService:
        def list_profiles(self, worker_id):
            assert worker_id == "bot:owner"
            return SimpleNamespace(items=[SimpleNamespace(profile_id="default")])

        def delete_profile(self, worker_id, profile_id):
            events.append(f"profile:{worker_id}:{profile_id}")

    def delete_worker(worker_id):
        events.append(f"worker:{worker_id}")
        return original_delete(worker_id)

    monkeypatch.setattr(lifecycle_routes, "_get_profile_service", ProfileService, raising=False)
    monkeypatch.setattr(workers, "delete", delete_worker)

    with TestClient(app) as client:
        unauthenticated = client.delete("/api/v1/workers/bot:owner")
        assert unauthenticated.status_code == 401

        response = client.delete(
            "/api/v1/workers/bot:owner",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "success": True,
        "worker_id": "bot:owner",
        "deleted": True,
    }
    assert events == ["profile:bot:owner:default", "worker:bot:owner"]
    assert workers.get_by_id("bot:owner") is None

    with TestClient(app) as client:
        missing = client.delete(
            "/api/v1/workers/bot:owner",
            headers={"Authorization": "Bearer test-token"},
        )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "WORKER_NOT_FOUND"


def test_profile_cleanup_failure_preserves_worker_for_retry(monkeypatch):
    monkeypatch.setenv("BCSFUSE_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    app = create_opensource_app(mode="test")
    workers = app.state.context.registry.get("worker_registry_store")
    workers.create(
        Worker(
            id="bot:owner",
            type=WorkerType.BOT,
            responsibilities=[],
            capabilities=[],
            identity=WorkerIdentity(name="Bot", handle="@bot:owner"),
            state=WorkerState(
                availability=Availability.PROTECTED,
                trust_level=TrustLevel.UNVERIFIED,
            ),
        )
    )

    class FailingProfileService:
        def list_profiles(self, worker_id):
            raise RuntimeError(f"profile cleanup failed for {worker_id}")

    monkeypatch.setattr(lifecycle_routes, "_get_profile_service", FailingProfileService)

    with TestClient(app) as client:
        response = client.delete(
            "/api/v1/workers/bot:owner",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 500
    assert workers.get_by_id("bot:owner") is not None
