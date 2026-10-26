"""Drive the actual application lifespan; do not hide DDL through test bootstrap."""

from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.app import app
from tests.community.core.space_knowledge.capture_fixtures import (
    missing_knowledge_graph as missing_knowledge_graph,
)


def test_full_lifespan_and_legacy_http_without_knowledge_table(
    missing_knowledge_graph, monkeypatch
):
    graph, _db = missing_knowledge_graph
    monkeypatch.setattr(app.state, "injector", graph)
    with TestClient(app) as client:
        response = client.post(
            "/api/spaces/initialize",
            json={
                "space_name": "No knowledge",
                "admin_user_id": "owner",
                "member_user_ids": ["bob"],
            },
        )
        assert response.status_code == 201, response.text
        sid = response.json()["data"]["space"]["id"]
        response = client.post(
            f"/api/spaces/{sid}/members/batch-add", json={"member_user_ids": ["carol"]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["added_user_ids"] == ["carol"]
        assert client.get(f"/api/spaces/{sid}").json()["data"]["member_count"] == 3
        response = client.post(f"/api/spaces/{sid}/delete-with-members")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["deleted"] is True


def test_default_full_lifespan_pilot_creates_space_without_knowledge_schema(
    missing_knowledge_graph, monkeypatch
):
    graph, _db = missing_knowledge_graph
    monkeypatch.setattr(app.state, "injector", graph)
    with TestClient(app) as client:
        response = client.post(
            "/api/spaces", json={"space_name": "Pilot", "admin_user_id": "owner"}
        )
        assert response.status_code == 201, response.text
        sid = response.json()["data"]["space"]["id"]
        assert client.get(f"/api/spaces/{sid}").json()["data"]["member_count"] == 1


def test_full_lifespan_does_not_swallow_enabled_missing_schema(
    missing_knowledge_graph, monkeypatch
):
    import pytest
    from agentclaw.community.di.space_knowledge_capture_config import (
        SpaceKnowledgeCaptureConfig,
    )

    graph, _db = missing_knowledge_graph
    graph.binder.bind(
        SpaceKnowledgeCaptureConfig, to=SpaceKnowledgeCaptureConfig(enabled=True)
    )
    monkeypatch.setattr(app.state, "injector", graph)
    with pytest.raises(RuntimeError, match="requires.*migration"):
        with TestClient(app):
            pytest.fail("must not serve without recording prerequisites")
