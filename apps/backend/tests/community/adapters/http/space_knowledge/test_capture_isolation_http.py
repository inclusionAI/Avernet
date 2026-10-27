"""Drive the actual application lifespan; do not hide DDL through test bootstrap."""

import pytest
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


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("schema_state", ["missing_table", "missing_column"])
def test_full_lifespan_serves_with_unusable_knowledge_schema(
    missing_knowledge_graph, monkeypatch, enabled, schema_state
):
    from agentclaw.community.di.space_knowledge_capture_config import (
        SpaceKnowledgeCaptureConfig,
    )

    graph, db = missing_knowledge_graph
    graph.binder.bind(
        SpaceKnowledgeCaptureConfig, to=SpaceKnowledgeCaptureConfig(enabled=enabled)
    )
    if schema_state == "missing_column":
        with db._engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE ac_space_knowledge_sync (env TEXT)"
            )
    try:
        monkeypatch.setattr(app.state, "injector", graph)
        with TestClient(app) as client:
            response = client.get("/api/spaces/1")
            assert response.status_code == 200, response.text
            assert response.json()["data"]["member_count"] == 2
            if not enabled:
                response = client.post(
                    "/api/spaces",
                    json={
                        "space_name": "No schema dependency",
                        "admin_user_id": "owner",
                    },
                )
                assert response.status_code == 201, response.text
    finally:
        if schema_state == "missing_column":
            with db._engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE ac_space_knowledge_sync")


@pytest.mark.parametrize("owner_app", ["agentclaw", "other-app"])
def test_full_lifespan_and_legacy_http_with_retained_knowledge_history(
    missing_knowledge_graph, monkeypatch, owner_app
):
    from agentclaw.community.core.space_knowledge.repository.models import (
        SpaceKnowledgeSyncModel as Snapshot,
    )

    graph, db = missing_knowledge_graph
    Snapshot.__table__.create(db._engine)
    try:
        with db.orm_session() as session:
            session.add(
                Snapshot(
                    env="pre",
                    space_id=7,
                    revision=1,
                    app=owner_app,
                    payload="{}",
                    snapshot_hash="x" * 64,
                    status="disabled",
                    acknowledged=True,
                )
            )
        monkeypatch.setattr(app.state, "injector", graph)
        with TestClient(app) as client:
            response = client.post(
                "/api/spaces",
                json={"space_name": "Available with history", "admin_user_id": "owner"},
            )
            assert response.status_code == 201, response.text
            sid = response.json()["data"]["space"]["id"]
            assert client.get(f"/api/spaces/{sid}").json()["data"]["member_count"] == 1
            response = client.post(
                f"/api/spaces/{sid}/members/batch-add",
                json={"member_user_ids": ["bob"]},
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["added_user_ids"] == ["bob"]
            assert client.get(f"/api/spaces/{sid}").json()["data"]["member_count"] == 2
        with db.orm_session() as session:
            row = session.query(Snapshot).one()
            assert (row.space_id, row.revision, row.app) == (7, 1, owner_app)
    finally:
        Snapshot.__table__.drop(db._engine)
