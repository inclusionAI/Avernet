"""BCS-facing worker lifecycle routes exposed by the OSS application."""

from fastapi.testclient import TestClient

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
    assert workers.get_by_id("bot:owner") is None
