"""Compatibility contract for the retired per-Skill Center scan route."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.skill_center.skill_scan import (
    router as skill_scan_router,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(skill_scan_router)
    return TestClient(app, raise_server_exceptions=False)


def test_nonempty_legacy_center_scan_points_to_canonical_g4_sync() -> None:
    response = _client().post(
        "/api/skill-scan/scan/center",
        json={"skill_uuids": ["uuid-aaa"], "env": "dev"},
    )

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Use POST /openapi/v1/bots/market/skill-center/sync"
    )


def test_empty_legacy_center_scan_keeps_its_input_guardrail() -> None:
    response = _client().post(
        "/api/skill-scan/scan/center",
        json={"skill_uuids": [], "env": "dev"},
    )

    assert response.status_code == 400
