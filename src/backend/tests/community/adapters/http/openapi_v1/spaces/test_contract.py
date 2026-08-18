"""Contract tests for the public Space/member/favorite HTTP surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
from agentclaw.community.core.market_favorites.errors import FavoriteNotFoundError
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketFavoriteRecord,
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
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)


@pytest.fixture
def member_service():
    service = MagicMock()
    service.add_member.side_effect = lambda **kwargs: SpaceMemberRecord(
        id=2,
        space_id=kwargs["space_id"],
        user_id=kwargs["user_id"].strip(),
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
def client(member_service, space_service, favorite_service):
    class _Bindings(Module):
        def configure(self, binder):
            binder.bind(SpaceMemberServiceProtocol, to=member_service)
            binder.bind(SpaceServiceProtocol, to=space_service)
            binder.bind(MarketFavoriteServiceProtocol, to=favorite_service)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner-1"}
    attach_injector(app, Injector([_Bindings()]))
    mount_public_error_handlers(app)
    return TestClient(app)



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

    response = client.get("/openapi/v1/spaces")

    assert response.status_code == 200
    assert (
        response.json()["data"]["items"][0]["gmt_modified"]
        == "2026-08-17T07:50:45Z"
    )

def test_naive_persisted_datetime_is_serialized_as_explicit_utc():
    item = SpaceMemberItem(
        user_id="member-1",
        role=SpaceRole.MEMBER,
        is_creator=False,
        gmt_modified=datetime(2026, 8, 17, 7, 50, 45),
    )

    assert item.model_dump(mode="json")["gmt_modified"] == "2026-08-17T07:50:45Z"


def test_aware_datetime_is_normalized_to_utc():
    item = MarketFavoriteItem(
        favorite_id=1,
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
        ({"user_id": "member-1"}, SpaceRole.MEMBER),
        ({"user_id": "owner-2", "role": "OWNER"}, SpaceRole.OWNER),
    ],
)
def test_add_member_accepts_an_optional_role(client, member_service, payload, expected_role):
    response = client.post("/openapi/v1/spaces/7/members", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 201000
    assert body["message"] == "Created"
    assert body["data"] == {
        "space_id": 7,
        "user_id": payload["user_id"],
        "role": expected_role.value,
    }
    assert member_service.add_member.call_args.kwargs["role"] is expected_role


def test_openapi_does_not_advertise_unavailable_profile_or_catalogue_fields(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    member_properties = schemas["SpaceMemberItem"]["properties"]
    assert "user_name" not in member_properties
    assert "display_name" not in member_properties
    assert "membership relation" in member_properties["gmt_modified"]["description"]
    assert member_properties["gmt_modified"]["format"] == "date-time"

    favorite_properties = schemas["MarketFavoriteItem"]["properties"]
    assert set(favorite_properties) == {
        "favorite_id",
        "target_type",
        "target_code",
        "favorite_at",
        "is_favorited",
    }


def test_cancel_missing_favorite_returns_not_found(client, favorite_service):
    favorite_service.cancel.side_effect = FavoriteNotFoundError(
        "market favorite not found"
    )

    response = client.post(
        "/openapi/v1/spaces/7/market-favorites/cancel",
        json={"target_type": "SKILL", "target_code": "skill-1"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == 404000
    assert body["message"] == "Not found"
    assert body["data"] is None



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


def _member_summary(user_id="member-1", role=SpaceRole.MEMBER):
    timestamp = datetime(2026, 8, 18, 1, 2, 3)
    return SpaceMemberSummaryRecord(
        member=SpaceMemberRecord(
            id=2,
            space_id=7,
            user_id=user_id,
            role=role,
            env="test",
            created_by="owner-1",
            gmt_created=timestamp,
            gmt_modified=timestamp,
        ),
        is_creator=user_id == "owner-1",
    )


def _favorite_record():
    timestamp = datetime(2026, 8, 18, 1, 2, 3)
    return MarketFavoriteRecord(
        id=31,
        space_id=7,
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
        "/openapi/v1/spaces",
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

    response = client.post("/openapi/v1/spaces/personal/initialize")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["space_type"] == "PERSONAL"
    assert data["created"] is was_created
    assert data["current_user_role"] == "OWNER"
    space_service.initialize_personal.assert_called_once_with(user_id="owner-1")


def test_create_team_space_returns_owner_metadata(client, space_service):
    space_service.create_team.return_value = _space_record()

    response = client.post(
        "/openapi/v1/spaces/create", json={"space_name": "Team"}
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["space_id"] == 7
    assert data["is_creator"] is True
    assert data["member_count"] == data["owner_count"] == 1
    space_service.create_team.assert_called_once_with(
        name="Team", creator_id="owner-1"
    )


def test_member_list_delete_and_role_update(client, member_service):
    member_service.list_members.return_value = (1, [_member_summary()])
    member_service.update_role.return_value = _member_summary(
        user_id="member-1", role=SpaceRole.OWNER
    )

    listed = client.get(
        "/openapi/v1/spaces/7/members",
        params={"keyword": "mem", "page_no": 2, "page_size": 10},
    )
    deleted = client.delete("/openapi/v1/spaces/7/members/member-1")
    updated = client.put(
        "/openapi/v1/spaces/7/members/member-1/role", json={"role": "OWNER"}
    )

    assert listed.status_code == deleted.status_code == updated.status_code == 200
    assert listed.json()["data"]["items"][0]["user_id"] == "member-1"
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
    favorite_service.add.return_value = _favorite_record()
    favorite_service.search.return_value = (1, [_favorite_record()])

    added = client.post(
        "/openapi/v1/spaces/7/market-favorites",
        json={"target_type": "SKILL", "target_code": "skill-1"},
    )
    canceled = client.post(
        "/openapi/v1/spaces/7/market-favorites/cancel",
        json={"target_type": "SKILL", "target_code": " skill-1 "},
    )
    searched = client.post(
        "/openapi/v1/spaces/7/market-favorites/search",
        json={
            "target_type": "SKILL",
            "keyword": "skill",
            "page_no": 2,
            "page_size": 5,
        },
    )

    assert added.status_code == canceled.status_code == searched.status_code == 200
    assert added.json()["data"]["is_favorited"] is True
    assert canceled.json()["data"] == {
        "target_type": "SKILL",
        "target_code": "skill-1",
        "is_favorited": False,
    }
    assert searched.json()["data"]["items"][0] == {
        "favorite_id": 31,
        "target_type": "SKILL",
        "target_code": "skill-1",
        "favorite_at": "2026-08-18T01:02:03Z",
        "is_favorited": True,
    }
    favorite_service.add.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
    )
    favorite_service.cancel.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        target_type=FavoriteTargetType.SKILL,
        target_code=" skill-1 ",
    )
    favorite_service.search.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        target_type=FavoriteTargetType.SKILL,
        keyword="skill",
        page_no=2,
        page_size=5,
    )
