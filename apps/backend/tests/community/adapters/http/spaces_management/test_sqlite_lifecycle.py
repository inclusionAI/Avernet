"""All eleven maintenance HTTP operations backed by real SQLite transactions."""

from datetime import datetime


from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)


def data(response, status=200):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == "OK"
    assert response.headers["X-Request-ID"] == body["request_id"]
    return body["data"]


def test_all_eleven_endpoints_lifecycle(harness):
    client, db, sc, _, _ = harness
    first = data(
        client.post(
            "/api/spaces",
            json={
                "space_name": "Standalone",
                "admin_user_id": "100001",
            },
        ),
        201,
    )["space"]["id"]
    sid = data(
        client.post(
            "/api/spaces/initialize",
            json={
                "space_name": "Initialized",
                "admin_user_id": "100010",
                "member_user_ids": ["100011", "100012", "100011", "100010"],
            },
        ),
        201,
    )["space"]["id"]
    assert data(client.get("/api/spaces"))["total"] == 2
    detail = data(client.get(f"/api/spaces/{sid}"))
    assert detail["member_count"] == 3 and detail["admin_count"] == 1
    assert detail["space"]["created_by"] == "100010"
    changed = data(
        client.post(f"/api/spaces/{sid}/update", json={"space_name": "Renamed"})
    )
    assert changed["changed"] is True
    assert changed["space"]["space"]["name"] == "Renamed"
    assert changed["space"]["space"]["updated_by"] == "operator"
    unchanged = data(
        client.post(f"/api/spaces/{sid}/update", json={"space_name": "Renamed"})
    )
    assert unchanged["changed"] is False
    added = data(
        client.post(
            f"/api/spaces/{sid}/members/batch-add",
            json={
                "member_user_ids": ["100012", "100013", "100013", "100010"],
            },
        )
    )
    assert added["added_user_ids"] == ["100013"]
    assert added["existing_user_ids"] == ["100012", "100010"]
    members = data(client.get(f"/api/spaces/{sid}/members"))
    assert members["total"] == 4
    roles = {row["user_id"]: row["role"] for row in members["items"]}
    assert roles == {
        "100010": "ADMIN",
        "100011": "MEMBER",
        "100012": "MEMBER",
        "100013": "MEMBER",
    }
    assert "operator" not in roles
    for ids, changed_ids, unchanged_ids in [
        (["100011"], ["100011"], []),
        (["100011", "100012", "100012"], ["100012"], ["100011"]),
    ]:
        result = data(
            client.put(
                f"/api/spaces/{sid}/members/roles",
                json={
                    "member_user_ids": ids,
                    "role": "ADMIN",
                },
            )
        )
        assert result["updated_user_ids"] == changed_ids
        assert result["unchanged_user_ids"] == unchanged_ids
    # Maintenance intentionally allows demoting the creator and removing the final admin.
    result = data(
        client.put(
            f"/api/spaces/{sid}/members/roles",
            json={
                "member_user_ids": ["100010", "100011", "100012"],
                "role": "MEMBER",
            },
        )
    )
    assert len(result["updated_user_ids"]) == 3
    assert data(client.get(f"/api/spaces/{sid}"))["admin_count"] == 0
    blocked = client.delete(f"/api/spaces/{sid}")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "SPACE_HAS_MEMBERS"
    assert blocked.json()["data"]["member_count"] == 4
    assert "先移除全部成员" in blocked.json()["message"]
    removed = data(
        client.post(
            f"/api/spaces/{first}/members/batch-remove",
            json={
                "member_user_ids": ["100001", "100001", "absent"],
            },
        )
    )
    assert removed["removed_user_ids"] == ["100001"]
    assert removed["missing_user_ids"] == ["absent"]
    repeat = data(
        client.post(
            f"/api/spaces/{first}/members/batch-remove",
            json={
                "member_user_ids": ["100001"],
            },
        )
    )
    assert repeat["removed_user_ids"] == []
    assert repeat["missing_user_ids"] == ["100001"]
    assert data(client.delete(f"/api/spaces/{first}"))["removed_member_count"] == 0
    assert (
        data(client.post(f"/api/spaces/{sid}/delete-with-members"))[
            "removed_member_count"
        ]
        == 4
    )
    assert client.get(f"/api/spaces/{sid}").status_code == 404
    with db.orm_session() as session:
        assert session.query(SpaceModel).count() == 0
        assert session.query(SpaceMemberModel).count() == 0
    # Rename/delete/member operations must not call SC.
    assert sc.create_team.call_count == 2
    assert len(sc.mock_calls) == 2


def test_list_filters_pagination_and_historical_members(harness, create):
    client, db, *_ = harness
    sid = create("Alpha", members=["200002", "200003", "200004"])
    create("Beta", admin="200000")
    with db.transactional_orm_session() as session:
        session.query(SpaceMemberModel).filter_by(
            space_id=sid, user_id="200002"
        ).update(
            {
                "role": "ADMINISTRATOR",
            }
        )
        session.query(SpaceMemberModel).filter_by(
            space_id=sid, user_id="200003"
        ).update(
            {
                "status": "INACTIVE",
                "removed_by": "old",
                "removed_at": datetime.now(),
            }
        )
    page = data(client.get("/api/spaces", params={"page_size": 1, "page_no": 2}))
    assert page["total"] == 2 and len(page["items"]) == 1
    assert data(client.get("/api/spaces", params={"page_no": 99}))["items"] == []
    filtered = data(
        client.get(
            "/api/spaces", params={"keyword": "Alp", "creator_user_id": "100001"}
        )
    )
    assert filtered["total"] == 1 and filtered["items"][0]["space"]["id"] == sid
    assert (
        data(client.get("/api/spaces", params={"creator_user_id": "missing"}))["total"]
        == 0
    )
    assert data(client.get(f"/api/spaces/{sid}"))["member_count"] == 3
    assert data(client.get(f"/api/spaces/{sid}"))["admin_count"] == 2
    admins = data(client.get(f"/api/spaces/{sid}/members", params={"role": "ADMIN"}))
    assert admins["total"] == 2
    assert all(row["role"] == "ADMIN" for row in admins["items"])
    assert sum(row["is_creator"] for row in admins["items"]) == 1
    query = data(
        client.get(
            f"/api/spaces/{sid}/members",
            params={
                "role": "MEMBER",
                "keyword": "200",
                "status": "ALL",
                "page_size": 1,
                "page_no": 2,
            },
        )
    )
    assert query["total"] == 2 and len(query["items"]) == 1
    inactive = data(
        client.get(f"/api/spaces/{sid}/members", params={"status": "INACTIVE"})
    )
    assert [m["user_id"] for m in inactive["items"]] == ["200003"]
    missing = client.put(
        f"/api/spaces/{sid}/members/roles",
        json={
            "member_user_ids": ["200004", "200003", "unknown"],
            "role": "ADMIN",
        },
    )
    assert missing.status_code == 409
    assert missing.json()["data"]["missing_user_ids"] == ["200003", "unknown"]
    with db.orm_session() as session:
        assert (
            session.query(SpaceMemberModel)
            .filter_by(space_id=sid, user_id="200004")
            .one()
            .role
            == "MEMBER"
        )
    result = data(
        client.post(
            f"/api/spaces/{sid}/members/batch-add",
            json={
                "member_user_ids": ["200003", "200002"],
            },
        )
    )
    assert result["reactivated_user_ids"] == ["200003"]
    assert result["existing_user_ids"] == ["200002"]
    with db.orm_session() as session:
        row = (
            session.query(SpaceMemberModel)
            .filter_by(space_id=sid, user_id="200003")
            .one()
        )
        assert row.role == "MEMBER" and row.status == "ACTIVE"
        assert row.removed_at is None and row.removed_by is None
    no_op = data(
        client.put(
            f"/api/spaces/{sid}/members/roles",
            json={
                "member_user_ids": ["200002"],
                "role": "ADMIN",
            },
        )
    )
    assert no_op["unchanged_user_ids"] == ["200002"]


def test_name_conflicts_are_atomic_and_follow_existing_scope(harness, create):
    client, db, sc, *_ = harness
    sid = create("Alpha")
    other = create("Beta")
    response = client.post(
        "/api/spaces", json={"space_name": "Alpha", "admin_user_id": "100001"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SPACE_NAME_ALREADY_EXISTS"
    assert sc.create_team.call_count == 2
    response = client.post(f"/api/spaces/{other}/update", json={"space_name": "Alpha"})
    assert response.status_code == 409
    assert data(client.get(f"/api/spaces/{other}"))["space"]["name"] == "Beta"
    create("Alpha", admin="different-creator")
    with db.transactional_orm_session() as session:
        session.query(SpaceModel).filter_by(id=sid).update(
            {"deleted_at": datetime.now()}
        )
    create("Alpha")


def test_inactive_members_block_empty_delete_and_are_cascade_deleted(harness, create):
    client, db, *_ = harness
    sid = create()
    with db.transactional_orm_session() as session:
        session.query(SpaceMemberModel).filter_by(space_id=sid).update(
            {"status": "INACTIVE"}
        )
    assert data(client.get(f"/api/spaces/{sid}/members"))["total"] == 0
    assert client.delete(f"/api/spaces/{sid}").status_code == 409
    assert (
        data(client.post(f"/api/spaces/{sid}/delete-with-members"))[
            "removed_member_count"
        ]
        == 1
    )
    with db.orm_session() as session:
        assert session.query(SpaceMemberModel).count() == 0


def test_authenticated_staff_attribution(harness, create):
    client, db, *_ = harness
    sid = create()
    data(client.post(f"/api/spaces/{sid}/update", json={"space_name": "App renamed"}))
    data(
        client.post(
            f"/api/spaces/{sid}/members/batch-add", json={"member_user_ids": ["200001"]}
        )
    )
    with db.orm_session() as session:
        assert (
            session.query(SpaceModel).filter_by(id=sid).one().updated_by == "operator"
        )
        assert (
            session.query(SpaceMemberModel)
            .filter_by(space_id=sid, user_id="200001")
            .one()
            .created_by
            == "operator"
        )
