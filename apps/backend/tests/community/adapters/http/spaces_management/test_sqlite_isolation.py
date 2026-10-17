"""Environment boundaries for every new HTTP entrypoint on SQLite."""

from datetime import datetime

import pytest

from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)

SCOPED_OPERATIONS = [
    ("GET", "", None),
    ("POST", "/update", {"space_name": "New name"}),
    ("DELETE", "", None),
    ("POST", "/delete-with-members", None),
    ("GET", "/members", None),
    ("POST", "/members/batch-add", {"member_user_ids": ["100002"]}),
    ("POST", "/members/batch-remove", {"member_user_ids": ["100001", "100002"]}),
    ("PUT", "/members/roles", {"member_user_ids": ["100001"], "role": "MEMBER"}),
]


def snapshot(db):
    with db.orm_session() as session:
        return (
            [
                tuple(getattr(row, col.name) for col in SpaceModel.__table__.columns)
                for row in session.query(SpaceModel).order_by(SpaceModel.id)
            ],
            [
                tuple(
                    getattr(row, col.name) for col in SpaceMemberModel.__table__.columns
                )
                for row in session.query(SpaceMemberModel).order_by(SpaceMemberModel.id)
            ],
        )


@pytest.mark.parametrize("method,suffix,payload", SCOPED_OPERATIONS)
@pytest.mark.parametrize(
    "target_state", ["foreign_env", "missing", "deleted", "personal"]
)
def test_scoped_operations_reject_inaccessible_space(
    harness, create, monkeypatch, method, suffix, payload, target_state
):
    client, db, sc, staff, _ = harness
    sid = create(members=["100002"])
    if target_state == "foreign_env":
        monkeypatch.setenv("SERVER_ENV", "prod")
    elif target_state == "missing":
        sid += 99999
    else:
        with db.transactional_orm_session() as session:
            row = session.query(SpaceModel).filter_by(id=sid).one()
            if target_state == "deleted":
                row.deleted_at = datetime.now()
            else:
                row.space_type = "PERSONAL"
                row.personal_owner_id = row.created_by
    before = snapshot(db)
    sc.reset_mock()
    staff.reset_mock()
    response = client.request(method, f"/api/spaces/{sid}{suffix}", json=payload)
    assert response.status_code == (409 if target_state == "personal" else 404), (
        response.text
    )
    assert response.json()["code"] == (
        "SPACE_TYPE_NOT_SUPPORTED" if target_state == "personal" else "SPACE_NOT_FOUND"
    )
    assert snapshot(db) == before
    sc.create_team.assert_not_called()
    staff.get_profile_by_work_no.assert_not_called()


@pytest.mark.parametrize(
    "server_env,stored_env",
    [
        ("dev", "dev"),
        ("stable", "dev"),
        ("pre", "pre"),
        ("prepub", "pre"),
        ("prod", "prod"),
        ("gray", "prod"),
        ("PROD", "prod"),
    ],
)
@pytest.mark.parametrize("path", ["/api/spaces", "/api/spaces/initialize"])
def test_creation_and_lists_use_existing_environment_resolution(
    harness, create, monkeypatch, server_env, stored_env, path
):
    client, db, *_ = harness
    for env in ("dev", "pre", "prod"):
        monkeypatch.setenv("SERVER_ENV", env)
        create(f"Seed {env}", admin="seed-admin")
    monkeypatch.setenv("SERVER_ENV", server_env)
    response = client.post(
        path,
        json={"space_name": "Created", "admin_user_id": "100001"},
        headers={"X-Env": "other", "SERVER_ENV": "other"},
    )
    assert response.status_code == 201, response.text
    sid = response.json()["data"]["space"]["id"]
    assert response.json()["data"]["space"]["env"] == stored_env
    listing = client.get("/api/spaces").json()["data"]
    assert listing["total"] == 2
    assert {item["space"]["env"] for item in listing["items"]} == {stored_env}
    members = client.get(f"/api/spaces/{sid}/members").json()["data"]
    assert members["total"] == 1 and members["items"][0]["env"] == stored_env
    with db.orm_session() as session:
        assert (
            session.query(SpaceMemberModel).filter_by(space_id=sid).one().env
            == stored_env
        )


@pytest.mark.parametrize("method,suffix,payload", SCOPED_OPERATIONS)
def test_operations_never_touch_foreign_env_members_even_with_same_space_id(
    harness, create, method, suffix, payload
):
    client, db, *_ = harness
    sid = create(members=["100002"])
    with db.transactional_orm_session() as session:
        session.add(
            SpaceMemberModel(
                space_id=sid,
                user_id="100001",
                role="ADMIN",
                status="ACTIVE",
                env="prod",
                created_by="foreign",
            )
        )
        session.add(
            SpaceMemberModel(
                space_id=sid,
                user_id="100002",
                role="ADMIN",
                status="ACTIVE",
                env="prod",
                created_by="foreign",
            )
        )
        if method == "DELETE":
            session.query(SpaceMemberModel).filter_by(space_id=sid, env="pre").delete()
    # Capture by ORM field names as well, so this assertion does not depend on column order.
    with db.orm_session() as session:
        foreign_ids = {
            row.id for row in session.query(SpaceMemberModel).filter_by(env="prod")
        }
    foreign_before = [row for row in snapshot(db)[1] if row[0] in foreign_ids]
    response = client.request(method, f"/api/spaces/{sid}{suffix}", json=payload)
    assert response.status_code == 200, response.text
    assert [row for row in snapshot(db)[1] if row[0] in foreign_ids] == foreign_before
    if method == "GET":
        result = response.json()["data"]
        if suffix == "/members":
            assert result["total"] == 2
            assert all(row["env"] == "pre" for row in result["items"])
        else:
            assert result["member_count"] == 2 and result["admin_count"] == 1


@pytest.mark.parametrize(
    "method,path,payload",
    [
        (
            "POST",
            "/api/spaces",
            {"space_name": "New", "admin_user_id": "100001", "env": "prod"},
        ),
        (
            "POST",
            "/api/spaces/initialize",
            {"space_name": "New", "admin_user_id": "100001", "env": "prod"},
        ),
        ("POST", "/api/spaces/{sid}/update", {"space_name": "New", "env": "prod"}),
        (
            "POST",
            "/api/spaces/{sid}/members/batch-add",
            {"member_user_ids": ["100002"], "env": "prod"},
        ),
        (
            "POST",
            "/api/spaces/{sid}/members/batch-remove",
            {"member_user_ids": ["100001"], "env": "prod"},
        ),
        (
            "PUT",
            "/api/spaces/{sid}/members/roles",
            {"member_user_ids": ["100001"], "role": "MEMBER", "env": "prod"},
        ),
        ("GET", "/api/spaces?env=prod", None),
        ("GET", "/api/spaces/{sid}/members?env=prod", None),
    ],
)
def test_request_cannot_select_environment(harness, create, method, path, payload):
    client, db, *_ = harness
    sid = create()
    before = snapshot(db)
    response = client.request(method, path.format(sid=sid), json=payload)
    assert response.status_code == 422, response.text
    assert snapshot(db) == before


def test_list_excludes_personal_and_deleted_spaces(harness, create):
    client, db, *_ = harness
    visible = create("Visible")
    deleted = create("Deleted")
    personal = create("Personal")
    with db.transactional_orm_session() as session:
        session.query(SpaceModel).filter_by(id=deleted).update(
            {"deleted_at": datetime.now()}
        )
        session.query(SpaceModel).filter_by(id=personal).update(
            {"space_type": "PERSONAL", "personal_owner_id": "100001"}
        )
    listing = client.get("/api/spaces").json()["data"]
    assert listing["total"] == 1
    assert listing["items"][0]["space"]["id"] == visible
