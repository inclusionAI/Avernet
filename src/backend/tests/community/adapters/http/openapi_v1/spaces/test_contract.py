"""Contract tests for the public Space/member/favorite HTTP surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.spaces.router import router
from agentclaw.community.adapters.http.openapi_v1.spaces.schemas import (
    MarketFavoriteItem,
    SpaceMemberItem,
)
from agentclaw.community.api.market_favorite_service import (
    MarketFavoriteServiceProtocol,
)
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.api.space_skill_query_service import (
    SpaceSkillQueryServiceProtocol,
)
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketFavoriteRecord,
    MarketSource,
)
from agentclaw.community.core.spaces.models import (
    SpaceJoinStatus,
    SpaceMemberRecord,
    SpaceMemberSummaryRecord,
    SpaceRecord,
    SpaceRole,
    SpaceSummaryRecord,
    SpaceType,
)
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)


@pytest.fixture
def member_service():
    service = MagicMock()
    service.add_member.side_effect = lambda **kwargs: SpaceMemberRecord(
        id=2,
        space_id=kwargs["space_id"],
        user_id=kwargs["user_id"].strip(),
        user_name=(
            kwargs["user_name"].strip()
            if kwargs["user_name"] and kwargs["user_name"].strip()
            else None
        ),
        role=kwargs["role"],
        env="test",
        created_by="owner-1",
        gmt_created=datetime(2026, 8, 17, 7, 50, 45),
        gmt_modified=datetime(2026, 8, 17, 7, 50, 45),
    )
    return service


@pytest.fixture
def space_service():
    return MagicMock()


@pytest.fixture
def favorite_service():
    return MagicMock()


@pytest.fixture
def skill_query_service():
    return MagicMock()


@pytest.fixture
def client(member_service, space_service, favorite_service, skill_query_service):
    class _Bindings(Module):
        def configure(self, binder):
            binder.bind(SpaceMemberServiceProtocol, to=member_service)
            binder.bind(SpaceServiceProtocol, to=space_service)
            binder.bind(MarketFavoriteServiceProtocol, to=favorite_service)
            binder.bind(SpaceSkillQueryServiceProtocol, to=skill_query_service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner-1"}
    attach_injector(app, Injector([_Bindings()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "owner-1")


def test_endpoint_serializes_persisted_datetime_with_utc_marker(client, space_service):
    timestamp = datetime(2026, 8, 17, 7, 50, 45)
    space_service.list_spaces.return_value = (
        1,
        [
            SpaceSummaryRecord(
                space=SpaceRecord(
                    id=7,
                    space_code="spc-7",
                    space_type=SpaceType.TEAM,
                    name="Team",
                    personal_owner_id=None,
                    env="test",
                    created_by="owner-1",
                    updated_by="owner-1",
                    gmt_created=timestamp,
                    gmt_modified=timestamp,
                ),
                current_user_role=SpaceRole.OWNER,
                join_status=SpaceJoinStatus.JOINED,
                member_count=1,
                owner_count=1,
            )
        ],
    )

    response = client.get("/openapi/v1/bots/spaces")

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["creator_user_id"] == "owner-1"
    assert item["gmt_modified"] == "2026-08-17T07:50:45Z"


def test_naive_persisted_datetime_is_serialized_as_explicit_utc():
    item = SpaceMemberItem(
        user_id="member-1",
        user_name=None,
        display_name=None,
        role=SpaceRole.MEMBER,
        is_creator=False,
        gmt_modified=datetime(2026, 8, 17, 7, 50, 45),
    )

    assert item.model_dump(mode="json")["gmt_modified"] == "2026-08-17T07:50:45Z"


def test_aware_datetime_is_normalized_to_utc():
    item = MarketFavoriteItem(
        favorite_id=1,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
        favorite_at=datetime(
            2026, 8, 17, 0, 50, 45, tzinfo=timezone(timedelta(hours=8))
        ),
    )

    assert item.model_dump(mode="json")["favorite_at"] == "2026-08-16T16:50:45Z"


@pytest.mark.parametrize(
    ("payload", "expected_role"),
    [
        ({"member_user_id": "member-1"}, SpaceRole.MEMBER),
        ({"member_user_id": "owner-2", "role": "OWNER"}, SpaceRole.OWNER),
    ],
)
def test_add_member_accepts_an_optional_role(
    client, member_service, payload, expected_role
):
    response = client.post("/openapi/v1/bots/spaces/7/members", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 201000
    assert body["message"] == "Created"
    assert body["data"] == {
        "space_id": 7,
        "user_id": payload["member_user_id"],
        "role": expected_role.value,
    }
    assert member_service.add_member.call_args.kwargs["role"] is expected_role


@pytest.mark.parametrize(
    ("member_user_name", "expected"),
    [
        (None, None),
        ("", ""),
        ("  Zhang San  ", "  Zhang San  "),
    ],
)
def test_add_member_maps_member_user_name_to_service(
    client, member_service, member_user_name, expected
):
    payload = {"member_user_id": "member-1", "role": "MEMBER"}
    if member_user_name is not None:
        payload["member_user_name"] = member_user_name

    response = client.post("/openapi/v1/bots/spaces/7/members", json=payload)

    assert response.status_code == 201
    assert member_service.add_member.call_args.kwargs["user_name"] == expected


def test_add_member_rejects_member_user_name_over_128_characters(client):
    response = client.post(
        "/openapi/v1/bots/spaces/7/members",
        json={"member_user_id": "member-1", "member_user_name": "x" * 129},
    )

    assert response.status_code == 422


def test_openapi_advertises_nullable_member_profile_fields(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    member_properties = schemas["SpaceMemberItem"]["properties"]
    assert "user_name" in member_properties
    assert "display_name" in member_properties
    assert "membership relation" in member_properties["gmt_modified"]["description"]
    assert member_properties["gmt_modified"]["format"] == "date-time"

    add_member_properties = schemas["AddSpaceMemberRequest"]["properties"]
    user_name_schema = add_member_properties["member_user_name"]
    assert any(
        option.get("type") == "string" and option.get("maxLength") == 128
        for option in user_name_schema["anyOf"]
    )

    favorite_properties = schemas["MarketFavoriteItem"]["properties"]
    assert set(favorite_properties) == {
        "favorite_id",
        "market_source",
        "target_type",
        "target_code",
        "favorite_at",
        "is_favorited",
    }


def test_cancel_missing_favorite_returns_idempotent_success(client, favorite_service):
    favorite_service.cancel.return_value = False

    response = client.post(
        "/openapi/v1/bots/spaces/7/market-favorites/cancel",
        json={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_code": "skill-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200000
    assert body["data"]["is_favorited"] is False
    assert body["data"]["changed"] is False


def test_list_space_skills_maps_page_and_forwards_search(client, skill_query_service):
    timestamp = datetime(2026, 8, 20, 3, 40)
    skill_query_service.list_space_skills.return_value = (
        1,
        [
            {
                "id": 10001,
                "skill_uuid": "0b1b5f8f-demo",
                "name": "Smart Form Parser",
                "description": "Parse complex forms",
                "status": "DEVELOPING",
                "draft_status": "EDITING",
                "space_type": "TEAM",
                "current_user_skill_role": None,
                "can_edit": False,
                "can_grant": False,
                "can_apply_edit": True,
                "gmt_created": timestamp,
                "gmt_modified": timestamp,
            }
        ],
    )

    response = client.get(
        "/openapi/v1/bots/spaces/7/skills",
        params={"keyword": "form", "page_no": 2, "page_size": 5},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "total": 1,
        "items": [
            {
                "skill_id": "10001",
                "skill_uuid": "0b1b5f8f-demo",
                "name": "Smart Form Parser",
                "description": "Parse complex forms",
                "status": "DEVELOPING",
                "draft_status": "EDITING",
                "space_type": "TEAM",
                "current_user_skill_role": None,
                "can_edit": False,
                "can_grant": False,
                "can_apply_edit": True,
                "gmt_created": "2026-08-20T03:40:00Z",
                "gmt_modified": "2026-08-20T03:40:00Z",
            }
        ],
    }
    skill_query_service.list_space_skills.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        keyword="form",
        page_no=2,
        page_size=5,
    )


@pytest.mark.parametrize(
    ("params", "expected_status"),
    [
        ({"page_no": 0}, 422),
        ({"page_size": 101}, 422),
        ({"keyword": "x" * 129}, 422),
    ],
)
def test_list_space_skills_validates_query_contract(client, params, expected_status):
    response = client.get("/openapi/v1/bots/spaces/7/skills", params=params)

    assert response.status_code == expected_status
    assert response.json()["code"] == 422000


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (SpaceAccessDeniedError("membership required"), 403, 403000, "Forbidden"),
        (SpaceNotFoundError("space not found"), 404, 404000, "Not found"),
    ],
)
def test_list_space_skills_returns_stable_space_error_codes(
    client, skill_query_service, error, status, code, message
):
    skill_query_service.list_space_skills.side_effect = error

    response = client.get("/openapi/v1/bots/spaces/7/skills")

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["message"] == message
    assert response.json()["data"] is None


def _space_record(space_type=SpaceType.TEAM):
    timestamp = datetime(2026, 8, 18, 1, 2, 3)
    return SpaceRecord(
        id=7,
        space_code="spc-7",
        space_type=space_type,
        name="Team" if space_type is SpaceType.TEAM else "Personal",
        personal_owner_id=("owner-1" if space_type is SpaceType.PERSONAL else None),
        env="test",
        created_by="owner-1",
        updated_by="owner-1",
        gmt_created=timestamp,
        gmt_modified=timestamp,
    )


def _member_summary(
    user_id="member-1",
    role=SpaceRole.MEMBER,
    user_name=None,
    display_name=None,
):
    timestamp = datetime(2026, 8, 18, 1, 2, 3)
    return SpaceMemberSummaryRecord(
        member=SpaceMemberRecord(
            id=2,
            space_id=7,
            user_id=user_id,
            user_name=user_name,
            role=role,
            env="test",
            created_by="owner-1",
            gmt_created=timestamp,
            gmt_modified=timestamp,
        ),
        is_creator=user_id == "owner-1",
        display_name=display_name,
    )


def _favorite_record():
    timestamp = datetime(2026, 8, 18, 1, 2, 3)
    return MarketFavoriteRecord(
        id=31,
        space_id=7,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
        created_by="owner-1",
        env="test",
        gmt_created=timestamp,
        gmt_modified=timestamp,
    )


def test_list_spaces_forwards_filters_and_maps_page(client, space_service):
    space_service.list_spaces.return_value = (
        1,
        [
            SpaceSummaryRecord(
                space=_space_record(),
                current_user_role=SpaceRole.OWNER,
                join_status=SpaceJoinStatus.JOINED,
                member_count=3,
                owner_count=1,
            )
        ],
    )

    response = client.get(
        "/openapi/v1/bots/spaces",
        params={"keyword": "team", "space_type": "TEAM", "page_no": 2, "page_size": 5},
    )

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert response.json()["data"]["items"][0]["space_id"] == 7
    space_service.list_spaces.assert_called_once_with(
        user_id="owner-1",
        keyword="team",
        space_type=SpaceType.TEAM,
        page_no=2,
        page_size=5,
    )


@pytest.mark.parametrize("was_created", [True, False])
def test_initialize_personal_space_exposes_created_state(
    client, space_service, was_created
):
    space_service.initialize_personal.return_value = (
        _space_record(SpaceType.PERSONAL),
        was_created,
    )

    response = client.post("/openapi/v1/bots/spaces/personal/initialize")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["space_type"] == "PERSONAL"
    assert data["created"] is was_created
    assert data["current_user_role"] == "ADMIN"
    space_service.initialize_personal.assert_called_once_with(user_id="owner-1")


def test_create_team_space_returns_owner_metadata(client, space_service):
    space_service.create_team.return_value = _space_record()

    response = client.post(
        "/openapi/v1/bots/spaces/create", json={"space_name": "Team"}
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["space_id"] == 7
    assert data["is_creator"] is True
    assert data["member_count"] == data["owner_count"] == 1
    space_service.create_team.assert_called_once_with(name="Team", creator_id="owner-1")


def test_member_list_delete_and_role_update(client, member_service):
    member_service.list_members.return_value = (
        1,
        [_member_summary(user_name="Zhang San", display_name="Xiao Ming")],
    )
    member_service.update_role.return_value = _member_summary(
        user_id="member-1", role=SpaceRole.OWNER
    )

    listed = client.get(
        "/openapi/v1/bots/spaces/7/members",
        params={"keyword": "mem", "page_no": 2, "page_size": 10},
    )
    deleted = client.delete("/openapi/v1/bots/spaces/7/members/member-1")
    updated = client.put(
        "/openapi/v1/bots/spaces/7/members/member-1/role", json={"role": "OWNER"}
    )

    assert listed.status_code == deleted.status_code == updated.status_code == 200
    listed_member = listed.json()["data"]["items"][0]
    assert listed_member["user_id"] == "member-1"
    assert listed_member["user_name"] == "Zhang San"
    assert listed_member["display_name"] == "Xiao Ming"
    assert deleted.json()["data"] == {
        "space_id": 7,
        "user_id": "member-1",
        "deleted": True,
    }
    assert updated.json()["data"]["role"] == "OWNER"
    member_service.list_members.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        keyword="mem",
        page_no=2,
        page_size=10,
    )
    member_service.delete_member.assert_called_once_with(
        space_id=7, actor_id="owner-1", user_id="member-1"
    )
    member_service.update_role.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        user_id="member-1",
        role=SpaceRole.OWNER,
    )


def test_favorite_add_cancel_and_search(client, favorite_service):
    favorite_service.add.return_value = (_favorite_record(), True)
    favorite_service.cancel.return_value = True
    favorite_service.search.return_value = (1, [_favorite_record()])

    added = client.post(
        "/openapi/v1/bots/spaces/7/market-favorites",
        json={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_code": "skill-1",
        },
    )
    canceled = client.post(
        "/openapi/v1/bots/spaces/7/market-favorites/cancel",
        json={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "target_code": " skill-1 ",
        },
    )
    searched = client.post(
        "/openapi/v1/bots/spaces/7/market-favorites/search",
        json={
            "market_source": "SKILLCENTER",
            "target_type": "SKILL",
            "keyword": "skill",
            "page_no": 2,
            "page_size": 5,
        },
    )

    assert added.status_code == canceled.status_code == searched.status_code == 200
    assert added.json()["data"]["is_favorited"] is True
    assert added.json()["data"]["changed"] is True
    assert canceled.json()["data"] == {
        "market_source": "SKILLCENTER",
        "target_type": "SKILL",
        "target_code": "skill-1",
        "is_favorited": False,
        "changed": True,
    }
    assert searched.json()["data"]["items"][0] == {
        "favorite_id": 31,
        "market_source": "SKILLCENTER",
        "target_type": "SKILL",
        "target_code": "skill-1",
        "favorite_at": "2026-08-18T01:02:03Z",
        "is_favorited": True,
    }
    favorite_service.add.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
    )
    favorite_service.cancel.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code=" skill-1 ",
    )
    favorite_service.search.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        keyword="skill",
        page_no=2,
        page_size=5,
    )


def test_batch_favorite_status_forwards_space_source_and_targets(
    client, favorite_service
):
    favorite_service.find_favorited_codes.return_value = ["skill-1"]

    response = client.post(
        "/openapi/v1/bots/spaces/7/market-favorites/status",
        json={
            "market_source": "TEAMCLAW",
            "target_type": "SKILL",
            "target_codes": ["skill-1", "skill-2"],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "market_source": "TEAMCLAW",
        "target_type": "SKILL",
        "favorited_target_codes": ["skill-1"],
    }
    favorite_service.find_favorited_codes.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        market_source=MarketSource.TEAMCLAW,
        target_type=FavoriteTargetType.SKILL,
        target_codes=["skill-1", "skill-2"],
    )
