"""Real transaction rollback and invalid batch contracts through HTTP."""

from contextlib import contextmanager

import pytest

from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterTeamCreateError,
    SkillCenterTeamCreateResult,
)
from agentclaw.community.plugin_api.staff_dept import StaffProfileLookupError

WRITE_OPERATIONS = [
    ("POST", "/api/spaces", {"space_name": "New", "admin_user_id": "100003"}),
    (
        "POST",
        "/api/spaces/initialize",
        {
            "space_name": "New",
            "admin_user_id": "100003",
            "member_user_ids": ["100004", "100005"],
        },
    ),
    ("POST", "/api/spaces/{sid}/update", {"space_name": "Renamed"}),
    ("DELETE", "/api/spaces/{sid}", None),
    ("POST", "/api/spaces/{sid}/delete-with-members", None),
    (
        "POST",
        "/api/spaces/{sid}/members/batch-add",
        {"member_user_ids": ["100003", "100004"]},
    ),
    (
        "POST",
        "/api/spaces/{sid}/members/batch-remove",
        {"member_user_ids": ["100001", "100002"]},
    ),
    (
        "PUT",
        "/api/spaces/{sid}/members/roles",
        {"member_user_ids": ["100001", "100002"], "role": "ADMIN"},
    ),
]


def state(db):
    with db.orm_session() as session:
        return (
            [
                (row.id, row.name, row.updated_by)
                for row in session.query(SpaceModel).order_by(SpaceModel.id)
            ],
            [
                (row.id, row.space_id, row.user_id, row.role, row.status)
                for row in session.query(SpaceMemberModel).order_by(SpaceMemberModel.id)
            ],
        )


@pytest.mark.parametrize("method,path,payload", WRITE_OPERATIONS)
def test_every_write_rolls_back_and_returns_error_on_transaction_failure(
    harness, create, monkeypatch, method, path, payload
):
    client, db, *_ = harness
    sid = create(members=["100002"])
    if method == "DELETE":
        with db.transactional_orm_session() as session:
            session.query(SpaceMemberModel).filter_by(space_id=sid).delete()
    before = state(db)
    transaction = db.transactional_orm_session

    @contextmanager
    def fail_before_commit():
        with transaction() as session:
            yield session
            # Ensure the SQL really executed before simulating a commit-boundary failure.
            session.flush()
            raise RuntimeError("sensitive database diagnostic must not leak")

    monkeypatch.setattr(db, "transactional_orm_session", fail_before_commit)
    response = client.request(method, path.format(sid=sid), json=payload)
    assert response.status_code == 500, response.text
    assert response.json()["code"] == "SPACE_OPERATION_FAILED"
    assert "sensitive database" not in response.text
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert state(db) == before


@pytest.mark.parametrize("path", ["/api/spaces", "/api/spaces/initialize"])
def test_skill_center_failure_rolls_back_space_and_all_members(harness, path):
    client, db, sc, *_ = harness
    sc.create_team.side_effect = SkillCenterTeamCreateError("private service detail")
    payload = {"space_name": "New", "admin_user_id": "100001"}
    if path.endswith("initialize"):
        payload["member_user_ids"] = ["100002", "100003"]
    response = client.post(path, json=payload)
    assert response.status_code == 502
    assert response.json()["code"] == "SKILL_CENTER_CREATE_FAILED"
    assert "private service" not in response.text
    assert state(db) == ([], [])
    sc.create_team.assert_called_once()


def test_actual_sqlite_integrity_error_rolls_back_and_is_not_name_conflict(
    harness, create
):
    client, db, sc, *_ = harness
    create()
    before = state(db)
    # SQLite's real SC/env UNIQUE constraint rejects the second binding.
    sc.create_team.side_effect = lambda _: SkillCenterTeamCreateResult(
        team_id="sc-team-1"
    )
    response = client.post(
        "/api/spaces/initialize",
        json={
            "space_name": "Different",
            "admin_user_id": "100010",
            "member_user_ids": ["100011", "100012"],
        },
    )
    assert response.status_code == 500
    assert response.json()["code"] == "SPACE_OPERATION_FAILED"
    assert state(db) == before


@pytest.mark.parametrize("failure", ["unavailable", "blank"])
def test_staff_lookup_degrades_without_breaking_creation_or_batch_add(harness, failure):
    client, db, _, staff, _ = harness
    if failure == "unavailable":
        staff.get_profile_by_work_no.side_effect = StaffProfileLookupError(
            "unavailable"
        )
    else:
        staff.get_profile_by_work_no.return_value.nick_name = "  "
    response = client.post(
        "/api/spaces/initialize",
        json={
            "space_name": "New",
            "admin_user_id": "100001",
            "member_user_ids": ["100002"],
        },
    )
    assert response.status_code == 201, response.text
    sid = response.json()["data"]["space"]["id"]
    response = client.post(
        f"/api/spaces/{sid}/members/batch-add", json={"member_user_ids": ["100003"]}
    )
    assert response.status_code == 200
    with db.orm_session() as session:
        rows = session.query(SpaceMemberModel).all()
        assert len(rows) == 3
        assert all(row.user_name is None for row in rows)


@pytest.mark.parametrize("operation", ["add", "remove", "roles"])
@pytest.mark.parametrize(
    "ids", [[], [" "], [None], [123], ["x" * 129], [str(i) for i in range(501)]]
)
def test_invalid_member_batches_have_no_partial_writes(harness, create, operation, ids):
    client, db, *_ = harness
    sid = create()
    before = state(db)
    method = "PUT" if operation == "roles" else "POST"
    suffix = "roles" if operation == "roles" else f"batch-{operation}"
    payload = {"member_user_ids": ids}
    if operation == "roles":
        payload["role"] = "ADMIN"
    response = client.request(
        method, f"/api/spaces/{sid}/members/{suffix}", json=payload
    )
    assert response.status_code == 422
    assert state(db) == before


@pytest.mark.parametrize("role", ["OWNER", "ADMINISTRATOR", "admin", "", None, 1])
def test_invalid_roles_are_rejected(harness, create, role):
    client, db, *_ = harness
    sid = create()
    before = state(db)
    response = client.put(
        f"/api/spaces/{sid}/members/roles",
        json={"member_user_ids": ["100001"], "role": role},
    )
    assert response.status_code == 422
    assert state(db) == before


@pytest.mark.parametrize("suffix", ["", "/1/members"])
@pytest.mark.parametrize(
    "query", ["page_no=0", "page_size=101", "page_size=0", "page_no=abc"]
)
def test_invalid_pagination(harness, suffix, query):
    client, *_ = harness
    assert client.get(f"/api/spaces{suffix}?{query}").status_code == 422


def test_maximum_batch_and_trimmed_duplicate_ids(harness, create):
    client, db, *_ = harness
    sid = create()
    ids = [str(i) for i in range(500)]
    response = client.post(
        f"/api/spaces/{sid}/members/batch-add", json={"member_user_ids": ids}
    )
    assert response.status_code == 200
    assert len(response.json()["data"]["added_user_ids"]) == 500
    response = client.put(
        f"/api/spaces/{sid}/members/roles",
        json={"member_user_ids": ids, "role": "ADMIN"},
    )
    assert len(response.json()["data"]["updated_user_ids"]) == 500
    response = client.post(
        f"/api/spaces/{sid}/members/batch-remove", json={"member_user_ids": ids}
    )
    assert len(response.json()["data"]["removed_user_ids"]) == 500
    response = client.post(
        f"/api/spaces/{sid}/members/batch-add",
        json={"member_user_ids": [" 100002 ", "100002"]},
    )
    assert response.json()["data"]["added_user_ids"] == ["100002"]
    with db.orm_session() as session:
        assert session.query(SpaceMemberModel).count() == 2
