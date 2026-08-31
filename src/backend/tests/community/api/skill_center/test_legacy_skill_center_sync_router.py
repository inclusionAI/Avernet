"""Compatibility contract for the retired per-Skill NAS sync route."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.skill_center.sync import router


def test_legacy_per_skill_sync_returns_explicit_migration_target() -> None:
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).post(
        "/api/v1/skill-center/sync",
        json={"skill_id": "7", "env": "dev", "version": "1.0.0"},
    )

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Use POST /openapi/v1/bots/market/skill-center/sync"
    )
