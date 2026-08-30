"""Compatibility contract for the retired legacy batch-sync writer."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.skill_center.batch_sync import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_every_batch_sync_surface_is_permanently_retired() -> None:
    client = _client()

    responses = (
        client.post(
            "/api/v1/skill-center/batch-sync",
            json={"skill_codes": ["legacy-writer"]},
        ),
        client.get(
            "/api/v1/skill-center/batch-sync",
            params={"skill_codes": "legacy-writer"},
        ),
        client.get("/api/v1/skill-center/batch-sync/status/old-task"),
        client.get("/api/v1/skill-center/batch-sync/report/old-task"),
    )

    assert {response.status_code for response in responses} == {410}
    for response in responses:
        assert response.json()["detail"] == (
            "Legacy batch sync is retired; use "
            "POST /openapi/v1/bots/market/skill-center/sync"
        )
