"""Internal perspective diagnostics must neither break nor leak into HTTP DTOs."""

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.domain.models.fusion_result import FusionResult, FusionTiming, Perspective
from src.interfaces.api import fusion_routes


@pytest.mark.parametrize("metadata", [{}, {"diagnostics": {"private_detail": "not-for-http"}}])
def test_fusion_response_excludes_internal_perspective_metadata(monkeypatch, metadata):
    now = datetime.now(timezone.utc)
    result = FusionResult(
        group_id="group-test", fusion_id="fusion-test", question="Review?",
        partial_success=False,
        perspectives=[Perspective(
            participant_id="bot:123:default", participant_type="bot",
            role="consultant", summary="Reviewed", status="completed", metadata=metadata,
        )],
        timing=FusionTiming(started_at=now, finished_at=now, duration_ms=0),
    )

    class Service:
        def fuse(self, request, group_id):
            assert group_id == "group-test"
            return result

    monkeypatch.setattr(fusion_routes, "get_service", lambda: Service())
    app = FastAPI()
    app.include_router(fusion_routes.router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.post("/api/v1/groups/group-test/fuse", json={
            "question": "Review?", "participants": ["bot:123:default"],
        })
    assert response.status_code == 200, response.text
    perspective = response.json()["perspectives"][0]
    assert perspective["summary"] == "Reviewed"
    assert perspective["participant_id"] == "bot:123:default"
    assert "metadata" not in perspective
    assert "not-for-http" not in response.text
