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
