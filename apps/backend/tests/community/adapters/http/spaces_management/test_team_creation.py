"""TEAM-only creation contract exercised over HTTP with real SQLite writes."""

import pytest

from agentclaw.community.core.spaces.management_models import (
    InitializeTeam,
    ManagementError,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)


@pytest.mark.parametrize("path", ["/api/spaces", "/api/spaces/initialize"])
@pytest.mark.parametrize(
    "space_type", ["PERSONAL", "OTHER", "team", "", " TEAM ", None, 1, True, [], {}]
)
def test_reject_non_team_without_side_effects(harness, path, space_type):
    client, db, sc, staff, _ = harness
    response = client.post(
        path,
        json={
            "space_name": "Example",
            "admin_user_id": "100001",
            "space_type": space_type,
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "SPACE_TYPE_INVALID"
    assert "space_type 必须为 TEAM" in response.json()["message"]
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    sc.create_team.assert_not_called()
    staff.get_profile_by_work_no.assert_not_called()
    with db.orm_session() as session:
        assert session.query(SpaceModel).count() == 0
        assert session.query(SpaceMemberModel).count() == 0


@pytest.mark.parametrize("path", ["/api/spaces", "/api/spaces/initialize"])
@pytest.mark.parametrize("explicit_type", [False, True])
def test_team_and_default_persist_team_in_server_environment(
    harness, path, explicit_type
):
    client, db, sc, _, _ = harness
    payload = {"space_name": "Example", "admin_user_id": "100001"}
    if explicit_type:
        payload["space_type"] = "TEAM"
    if path.endswith("initialize"):
        payload["member_user_ids"] = ["100002"]
    response = client.post(path, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["data"]["space"]["space_type"] == "TEAM"
    sc.create_team.assert_called_once()
    with db.orm_session() as session:
        space = session.query(SpaceModel).one()
        assert space.space_type == "TEAM"
        assert space.env == "pre"
        assert space.created_by == "100001"
        members = session.query(SpaceMemberModel).all()
        assert len(members) == (2 if path.endswith("initialize") else 1)
        assert all(member.env == "pre" for member in members)


def test_service_rejects_validation_bypass_before_dependencies(harness):
    _, db, sc, staff, service = harness
    command = InitializeTeam.model_construct(
        space_name="Example",
        admin_user_id="100001",
        space_type="PERSONAL",
    )
    with pytest.raises(ManagementError, match="SPACE_TYPE_INVALID"):
        service.create_team(command)
    sc.create_team.assert_not_called()
    staff.get_profile_by_work_no.assert_not_called()
    with db.orm_session() as session:
        assert session.query(SpaceModel).count() == 0


def test_other_validation_errors_keep_generic_message(harness):
    client, _, sc, _, _ = harness
    response = client.post("/api/spaces", json={"space_type": "TEAM"})
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_INVALID"
    sc.create_team.assert_not_called()


def test_openapi_declares_team_only_with_default(harness):
    client, *_ = harness
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in ("CreateTeam", "InitializeTeam"):
        field = schemas[name]["properties"]["space_type"]
        assert field["const"] == "TEAM"
        assert field["default"] == "TEAM"
