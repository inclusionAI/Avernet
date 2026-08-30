"""Contract tests for the public Space/member/favorite HTTP surface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.spaces.router import router
from agentclaw.community.adapters.http.openapi_v1.spaces.skill_routes import (
    router as skill_router,
)
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
from agentclaw.community.api.space_skill_grant_service import (
    SpaceSkillGrantServiceProtocol,
)
from agentclaw.community.api.space_skill_application_service import (
    DraftDeleteOutcome,
    DraftFileContent,
    DraftFileItem,
    DraftFileTree,
    DraftMutationResult,
    SpaceSkillApplicationServiceProtocol,
    SpaceSkillCreationOutcome,
)
from agentclaw.community.plugin_api.space_skill_source import (
    ExactSkillPackageFetchError,
)
from agentclaw.community.api.space_skill_version_query_service import (
    SpaceSkillVersionQueryServiceProtocol,
)
from agentclaw.community.api.space_skill_editor_request_service import (
    SpaceSkillEditorRequestServiceProtocol,
)
from agentclaw.community.core.work_orders.models import WorkOrderRecord, WorkOrderStatus
from agentclaw.community.api.draft_edit_lease_service import (
    DraftEditLeaseServiceProtocol,
)
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketFavoriteRecord,
    MarketSource,
)
from agentclaw.community.core.skill_center.skill_package import (
    MAX_FILE_BYTES,
    SkillManifestMissingError,
    SkillManifestMultipleError,
    SkillPathInvalidError,
)
from agentclaw.community.core.spaces.models import (
    SpaceJoinStatus,
    SpaceListScope as DomainSpaceListScope,
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
from agentclaw.community.core.skill_center.errors import (
    SpaceSkillGrantConflictError,
    SpaceSkillGrantForbiddenError,
    SpaceSkillGrantMemberRequiredError,
    SpaceSkillGrantNotFoundError,
    SpaceSkillGrantReasonRequiredError,
    DraftEditLeaseConflictError,
    DraftEditLeaseForbiddenError,
    DraftEditLeaseNotFoundError,
    DraftEditLeaseTokenRejectedError,
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
        user_name="backend-resolved-name",
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
def skill_grant_service():
    return MagicMock()


@pytest.fixture
def skill_application_service():
    return MagicMock()


@pytest.fixture
def skill_version_query_service():
    return MagicMock()


@pytest.fixture
def skill_editor_request_service():
    return MagicMock()


@pytest.fixture
def draft_edit_lease_service():
    return MagicMock()


@pytest.fixture
def client(
    member_service,
    space_service,
    favorite_service,
    skill_query_service,
    skill_grant_service,
    skill_application_service,
    skill_version_query_service,
    skill_editor_request_service,
    draft_edit_lease_service,
):
    class _Bindings(Module):
        def configure(self, binder):
            binder.bind(SpaceMemberServiceProtocol, to=member_service)
            binder.bind(SpaceServiceProtocol, to=space_service)
            binder.bind(MarketFavoriteServiceProtocol, to=favorite_service)
            binder.bind(SpaceSkillQueryServiceProtocol, to=skill_query_service)
            binder.bind(SpaceSkillGrantServiceProtocol, to=skill_grant_service)
            binder.bind(
                SpaceSkillApplicationServiceProtocol, to=skill_application_service
            )
            binder.bind(
                SpaceSkillVersionQueryServiceProtocol,
                to=skill_version_query_service,
            )
            binder.bind(
                SpaceSkillEditorRequestServiceProtocol,
                to=skill_editor_request_service,
            )
            binder.bind(DraftEditLeaseServiceProtocol, to=draft_edit_lease_service)

    app = FastAPI()
    app.include_router(router)
    app.include_router(skill_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "owner-1"}
    attach_injector(app, Injector([_Bindings()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, "owner-1")


def test_skill_editor_request_publishes_stable_wire_and_uses_current_user(
    client, skill_editor_request_service
):
    now = datetime(2026, 8, 26, 8, 0, 0)
    skill_editor_request_service.create_request.return_value = WorkOrderRecord(
        id=91,
        work_order_no="WO-91",
        biz_type="SKILL_COLLABORATOR",
        biz_id="9",
        applicant_user_id="owner-1",
        apply_reason="共同维护",
        status=WorkOrderStatus.PENDING,
        reviewer_user_id=None,
        review_remark=None,
        reviewed_at=None,
        env="test",
        gmt_created=now,
        gmt_modified=now,
    )

    response = client.post(
        "/openapi/v1/bots/spaces/7/skills/9/editor-requests",
        json={"reason": "共同维护"},
    )

    assert response.status_code == 201
    assert response.json()["data"] == {
        "work_order_id": 91,
        "work_order_no": "WO-91",
        "status": "PENDING",
    }
    skill_editor_request_service.create_request.assert_called_once_with(
        space_id=7,
        skill_id=9,
        applicant_user_id="owner-1",
        reason="共同维护",
    )


def test_draft_edit_lease_endpoints_publish_fenced_resource_contract(
    client, draft_edit_lease_service
):
    draft_edit_lease_service.get_lease.return_value = {
        "required": True,
        "state": "HELD_BY_OTHER",
        "holder_user_id": "manager-1",
        "fencing_token": None,
    }
    draft_edit_lease_service.acquire.return_value = {
        "required": True,
        "state": "HELD_BY_ME",
        "holder_user_id": "owner-1",
        "fencing_token": 41,
    }
    draft_edit_lease_service.release.return_value = {
        "required": True,
        "state": "FREE",
        "holder_user_id": None,
        "fencing_token": None,
    }
    draft_edit_lease_service.takeover.return_value = {
        "required": True,
        "state": "HELD_BY_ME",
        "holder_user_id": "owner-1",
        "fencing_token": 42,
    }

    read = client.get("/openapi/v1/bots/spaces/7/skills/9/draft/lease")
    acquired = client.put("/openapi/v1/bots/spaces/7/skills/9/draft/lease")
    released = client.delete(
        "/openapi/v1/bots/spaces/7/skills/9/draft/lease?fencing_token=41"
    )
    taken = client.post("/openapi/v1/bots/spaces/7/skills/9/draft/lease/takeover")

    assert read.json()["data"]["fencing_token"] is None
    assert acquired.json()["data"]["fencing_token"] == 41
    assert released.json()["data"]["state"] == "FREE"
    assert taken.json()["data"]["fencing_token"] == 42
    draft_edit_lease_service.release.assert_called_once_with(
        space_id=7, skill_id=9, actor_id="owner-1", fencing_token=41
    )


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (DraftEditLeaseForbiddenError(), 403, 403204, "Forbidden"),
        (DraftEditLeaseNotFoundError(), 404, 404202, "Not found"),
        (
            DraftEditLeaseConflictError(),
            409,
            409303,
            "Draft edit Lease is already held",
        ),
        (
            DraftEditLeaseTokenRejectedError(),
            409,
            409304,
            "Draft edit Lease fencing token was rejected",
        ),
    ],
)
def test_draft_edit_lease_returns_stable_error_codes(
    client, draft_edit_lease_service, error, status, code, message
):
    draft_edit_lease_service.acquire.side_effect = error

    response = client.put("/openapi/v1/bots/spaces/7/skills/9/draft/lease")

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["message"] == message


def test_grant_endpoints_publish_stable_wire_and_delegate_actor(
    client, skill_grant_service
):
    skill_grant_service.list_grants.return_value = {
        "owner": {"user_id": "owner-1", "role": "OWNER"},
        "managers": [{"user_id": "manager-1", "role": "MANAGER"}],
        "actor": {
            "skill_role": "OWNER",
            "permissions": {
                "edit_draft": True,
                "publish_draft": True,
                "delete_draft": True,
                "create_upgrade_draft": True,
                "offline_skill": True,
                "manage_grants": True,
                "transfer_owner": True,
                "request_edit_access": False,
                "takeover_lease": True,
            },
        },
    }
    skill_grant_service.add_manager.return_value = {
        "user_id": "manager-2",
        "role": "MANAGER",
    }
    skill_grant_service.remove_manager.return_value = {
        "user_id": "manager-2",
        "role": "MANAGER",
    }
    skill_grant_service.transfer_owner.return_value = {
        "owner": {"user_id": "manager-1", "role": "OWNER"},
        "managers": [],
        "actor": {
            "skill_role": None,
            "permissions": {
                "edit_draft": False,
                "publish_draft": False,
                "delete_draft": False,
                "create_upgrade_draft": False,
                "offline_skill": False,
                "manage_grants": False,
                "transfer_owner": False,
                "request_edit_access": True,
                "takeover_lease": False,
            },
        },
    }

    grants = client.get("/openapi/v1/bots/spaces/7/skills/9/grants")
    added = client.put("/openapi/v1/bots/spaces/7/skills/9/managers/manager-2")
    removed = client.delete("/openapi/v1/bots/spaces/7/skills/9/managers/manager-2")
    transferred = client.post(
        "/openapi/v1/bots/spaces/7/skills/9/owner-transfer",
        json={"new_owner_user_id": "manager-1"},
    )

    assert grants.status_code == 200
    assert grants.json()["data"]["actor"]["permissions"]["manage_grants"] is True
    assert added.json()["data"] == {"user_id": "manager-2", "role": "MANAGER"}
    assert removed.json()["data"] == {"user_id": "manager-2", "role": "MANAGER"}
    assert transferred.json()["data"]["owner"]["user_id"] == "manager-1"
    skill_grant_service.list_grants.assert_called_once_with(
        space_id=7, skill_id=9, actor_id="owner-1"
    )
    skill_grant_service.transfer_owner.assert_called_once_with(
        space_id=7,
        skill_id=9,
        actor_id="owner-1",
        new_owner_user_id="manager-1",
        reason=None,
        retain_previous_owner_as_manager=False,
    )


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (SpaceSkillGrantForbiddenError(), 403, 403203, "Forbidden"),
        (SpaceSkillGrantNotFoundError(), 404, 404201, "Not found"),
        (
            SpaceSkillGrantMemberRequiredError(),
            409,
            409301,
            "Active Space membership required",
        ),
        (
            SpaceSkillGrantConflictError(),
            409,
            409302,
            "Skill Grant state conflicts with this operation",
        ),
        (
            SpaceSkillGrantReasonRequiredError(),
            422,
            422201,
            "Owner transfer reason is required",
        ),
    ],
)
def test_grant_endpoints_return_stable_error_codes(
    client, skill_grant_service, error, status, code, message
):
    skill_grant_service.list_grants.side_effect = error

    response = client.get("/openapi/v1/bots/spaces/7/skills/9/grants")

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["message"] == message


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


def test_add_member_ignores_legacy_member_user_name(client, member_service):
    response = client.post(
        "/openapi/v1/bots/spaces/7/members",
        json={
            "member_user_id": "member-1",
            "member_user_name": "forged-name",
            "role": "MEMBER",
        },
    )

    assert response.status_code == 201
    kwargs = member_service.add_member.call_args.kwargs
    assert kwargs["user_id"] == "member-1"
    assert "user_name" not in kwargs


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
    assert "ignored" in user_name_schema["description"]
    assert "member_user_id" in user_name_schema["description"]

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
                "lifecycle_status": "DRAFT_ONLY",
                "space_type": "TEAM",
                "owner": {"user_id": "owner-1", "display_name": "Owner One"},
                "latest_published_version": None,
                "draft": {
                    "target_version": 1,
                    "status": "EDITING",
                    "revision_id": "22222222-2222-4222-8222-222222222222",
                },
                "active_publication": None,
                "actor": {
                    "skill_role": None,
                    "permissions": {
                        "edit_draft": False,
                        "publish_draft": False,
                        "delete_draft": False,
                        "create_upgrade_draft": False,
                        "offline_skill": False,
                        "manage_grants": False,
                        "transfer_owner": False,
                        "request_edit_access": True,
                        "takeover_lease": False,
                    },
                    "pending_editor_request": None,
                },
                "lease_summary": {
                    "required": True,
                    "state": "FREE",
                    "holder_user_id": None,
                    "holder_display_name": None,
                },
                "gmt_created": timestamp,
                "gmt_modified": timestamp,
            }
        ],
    )

    response = client.get(
        "/openapi/v1/bots/spaces/7/skills",
        params={"keyword": "form", "page": 2, "page_size": 5},
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
                "lifecycle_status": "DRAFT_ONLY",
                "space_type": "TEAM",
                "owner": {"user_id": "owner-1", "display_name": "Owner One"},
                "latest_published_version": None,
                "draft": {
                    "target_version": 1,
                    "status": "EDITING",
                    "revision_id": "22222222-2222-4222-8222-222222222222",
                },
                "active_publication": None,
                "actor": {
                    "skill_role": None,
                    "permissions": {
                        "edit_draft": False,
                        "publish_draft": False,
                        "delete_draft": False,
                        "create_upgrade_draft": False,
                        "offline_skill": False,
                        "manage_grants": False,
                        "transfer_owner": False,
                        "request_edit_access": True,
                        "takeover_lease": False,
                    },
                    "pending_editor_request": None,
                },
                "lease_summary": {
                    "required": True,
                    "state": "FREE",
                    "holder_user_id": None,
                    "holder_display_name": None,
                },
                "gmt_created": "2026-08-20T03:40:00Z",
                "gmt_modified": "2026-08-20T03:40:00Z",
            }
        ],
    }
    skill_query_service.list_space_skills.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        keyword="form",
        page=2,
        page_size=5,
    )


@pytest.mark.parametrize(
    ("params", "expected_status"),
    [
        ({"page": 0}, 422),
        ({"page_size": 101}, 422),
        ({"keyword": "x" * 129}, 422),
    ],
)
def test_list_space_skills_validates_query_contract(client, params, expected_status):
    response = client.get("/openapi/v1/bots/spaces/7/skills", params=params)

    assert response.status_code == expected_status
    assert response.json()["code"] == 422000


def _skill_detail_record():
    timestamp = datetime(2026, 8, 30, 8)
    return {
        "id": 51,
        "skill_uuid": "11111111-1111-4111-8111-111111111111",
        "name": "draft-skill",
        "description": "Draft description",
        "lifecycle_status": "DRAFT_ONLY",
        "space_type": "TEAM",
        "owner": {"user_id": "owner-1", "display_name": "Owner One"},
        "latest_published_version": None,
        "draft": {
            "target_version": 1,
            "status": "EDITING",
            "revision_id": "22222222-2222-4222-8222-222222222222",
            "name": "draft-skill",
            "description": "Draft description",
            "source_kind": "FOLDER",
            "source_repo_url": None,
            "source_branch": None,
            "source_commit_sha": None,
            "source_subdir": None,
        },
        "active_publication": None,
        "actor": {
            "skill_role": "OWNER",
            "permissions": {
                "edit_draft": True,
                "publish_draft": True,
                "delete_draft": True,
                "create_upgrade_draft": True,
                "offline_skill": True,
                "manage_grants": True,
                "transfer_owner": True,
                "request_edit_access": False,
                "takeover_lease": True,
            },
            "pending_editor_request": None,
        },
        "lease_summary": {
            "required": True,
            "state": "FREE",
            "holder_user_id": None,
            "holder_display_name": None,
        },
        "source": "FOLDER",
        "offline_at": None,
        "offline_by": None,
        "gmt_created": timestamp,
        "gmt_modified": timestamp,
    }


def test_folder_and_git_creation_publish_real_idempotent_routes(
    client, skill_application_service, skill_query_service, monkeypatch
):
    from starlette.concurrency import run_in_threadpool as actual_run_in_threadpool

    offload = AsyncMock(side_effect=actual_run_in_threadpool)
    monkeypatch.setattr(
        "agentclaw.community.adapters.http.openapi_v1.spaces.skill_routes.run_in_threadpool",
        offload,
    )
    skill_application_service.create_from_folder.return_value = (
        SpaceSkillCreationOutcome(skill_id=51, created=True)
    )
    skill_application_service.create_from_git.return_value = SpaceSkillCreationOutcome(
        skill_id=51, created=True
    )
    skill_query_service.get_space_skill.return_value = _skill_detail_record()

    folder = client.post(
        "/openapi/v1/bots/spaces/7/skills",
        headers={"Idempotency-Key": "create-1"},
        files=[
            ("files", ("SKILL.md", b"manifest", "text/markdown")),
            ("files", ("example.md", b"example", "text/markdown")),
        ],
        data={"file_paths": '["draft-skill/SKILL.md","draft-skill/example.md"]'},
    )
    imported = client.post(
        "/openapi/v1/bots/spaces/7/skills/import-from-git",
        headers={"Idempotency-Key": "git-1"},
        json={"git_url": "https://example.com/team/skills.git"},
    )

    assert folder.status_code == imported.status_code == 201
    assert folder.json()["data"]["draft"]["revision_id"].endswith("222222222222")
    skill_application_service.create_from_folder.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        request_id="create-1",
        files=[
            ("draft-skill/SKILL.md", b"manifest"),
            ("draft-skill/example.md", b"example"),
        ],
    )
    skill_application_service.create_from_git.assert_called_once_with(
        space_id=7,
        actor_id="owner-1",
        request_id="git-1",
        git_url="https://example.com/team/skills.git",
        branch=None,
        subdir=None,
    )
    assert offload.await_count == 2
    assert offload.await_args_list[0].args == (
        skill_application_service.create_from_git,
    )


def test_folder_creation_rejects_oversized_upload_before_application_service(
    client, skill_application_service
):
    response = client.post(
        "/openapi/v1/bots/spaces/7/skills",
        headers={"Idempotency-Key": "create-too-large"},
        files=[
            (
                "files",
                ("SKILL.md", b"x" * (MAX_FILE_BYTES + 1), "application/octet-stream"),
            )
        ],
        data={"file_paths": '["SKILL.md"]'},
    )

    assert response.status_code == 422
    skill_application_service.create_from_folder.assert_not_called()


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SkillManifestMissingError(), 422205),
        (SkillManifestMultipleError(), 422206),
        (SkillPathInvalidError(), 422207),
    ],
)
def test_folder_creation_publishes_specific_package_error_codes(
    client, skill_application_service, error, code
):
    skill_application_service.create_from_folder.side_effect = error

    response = client.post(
        "/openapi/v1/bots/spaces/7/skills",
        headers={"Idempotency-Key": "create-invalid"},
        files=[("files", ("SKILL.md", b"manifest", "text/markdown"))],
        data={"file_paths": '["SKILL.md"]'},
    )

    assert response.status_code == 422
    assert response.json()["code"] == code


def test_draft_file_routes_preserve_revision_and_fencing_contract(
    client, skill_application_service
):
    skill_application_service.get_draft_file_tree.return_value = DraftFileTree(
        revision_id="rev-1", files=(DraftFileItem(path="SKILL.md", size=10),)
    )
    skill_application_service.read_draft_file.return_value = DraftFileContent(
        path="SKILL.md", content="# Skill", revision_id="rev-1"
    )
    skill_application_service.save_draft_file.return_value = DraftMutationResult(
        target_version=1,
        status="EDITING",
        revision_id="rev-2",
        name="draft-skill",
        description="Draft description",
        source_kind="FOLDER",
        source_repo_url=None,
        source_branch=None,
        source_commit_sha=None,
        source_subdir=None,
    )

    tree = client.get("/openapi/v1/bots/spaces/7/skills/51/draft/files")
    file = client.get("/openapi/v1/bots/spaces/7/skills/51/draft/files/SKILL.md")
    saved = client.put(
        "/openapi/v1/bots/spaces/7/skills/51/draft/files/SKILL.md",
        json={
            "content": "# Updated",
            "expected_revision_id": "rev-1",
            "fencing_token": 7,
        },
    )

    assert tree.json()["data"] == {
        "revision_id": "rev-1",
        "files": [{"path": "SKILL.md", "size": 10}],
    }
    assert file.json()["data"]["content"] == "# Skill"
    assert saved.json()["data"]["revision_id"] == "rev-2"
    skill_application_service.save_draft_file.assert_called_once_with(
        space_id=7,
        skill_id=51,
        actor_id="owner-1",
        path="SKILL.md",
        content="# Updated",
        expected_revision_id="rev-1",
        fencing_token=7,
    )


def test_upgrade_refresh_and_delete_routes_publish_command_contracts(
    client, skill_application_service
):
    mutation = DraftMutationResult(
        target_version=2,
        status="EDITING",
        revision_id="rev-2",
        name="draft-skill",
        description="Draft description",
        source_kind="PUBLISHED_VERSION",
        source_repo_url=None,
        source_branch=None,
        source_commit_sha=None,
        source_subdir=None,
    )
    skill_application_service.create_upgrade_draft.return_value = mutation
    skill_application_service.refresh_draft_from_git.return_value = mutation
    skill_application_service.delete_draft.return_value = DraftDeleteOutcome(
        changed=True, deleted_scope="DRAFT"
    )

    upgraded = client.post(
        "/openapi/v1/bots/spaces/7/skills/51/draft/upgrade",
        headers={"Idempotency-Key": "upgrade-2"},
    )
    refreshed = client.post(
        "/openapi/v1/bots/spaces/7/skills/51/draft/refresh-from-git",
        json={"expected_revision_id": "rev-1", "fencing_token": 7},
    )
    deleted = client.delete(
        "/openapi/v1/bots/spaces/7/skills/51/draft",
        params={"expected_revision_id": "rev-2", "fencing_token": 7},
    )

    assert upgraded.status_code == 201
    assert refreshed.json()["data"]["revision_id"] == "rev-2"
    assert deleted.json()["data"] == {"changed": True, "deleted_scope": "DRAFT"}
    skill_application_service.create_upgrade_draft.assert_called_once_with(
        space_id=7,
        skill_id=51,
        actor_id="owner-1",
        request_id="upgrade-2",
    )
    skill_application_service.refresh_draft_from_git.assert_called_once_with(
        space_id=7,
        skill_id=51,
        actor_id="owner-1",
        expected_revision_id="rev-1",
        fencing_token=7,
    )
    skill_application_service.delete_draft.assert_called_once_with(
        space_id=7,
        skill_id=51,
        actor_id="owner-1",
        expected_revision_id="rev-2",
        fencing_token=7,
    )


def test_upgrade_maps_exact_source_failure_to_sc_unavailable(
    client, skill_application_service
):
    skill_application_service.create_upgrade_draft.side_effect = (
        ExactSkillPackageFetchError("download failed")
    )

    response = client.post(
        "/openapi/v1/bots/spaces/7/skills/51/draft/upgrade",
        headers={"Idempotency-Key": "upgrade-failed"},
    )

    assert response.status_code == 502
    assert response.json()["code"] == 502000


def test_published_version_and_consumable_routes_use_business_ordinals(
    client, skill_version_query_service
):
    published_at = datetime(2026, 8, 30, 8)
    version = {
        "version": 2,
        "sc_version_number": "2.0.0",
        "name": "risk-review",
        "description": "Published",
        "mcp_dependencies": ["mcp.a"],
        "published_at": published_at,
    }
    skill_version_query_service.list_versions.return_value = (1, [version])
    skill_version_query_service.get_version.return_value = version
    skill_version_query_service.get_version_file_tree.return_value = {
        "version": 2,
        "files": [{"path": "SKILL.md", "size": 10}],
    }
    skill_version_query_service.read_version_file.return_value = {
        "version": 2,
        "path": "SKILL.md",
        "content": "# Published",
    }
    skill_version_query_service.list_consumable.return_value = (
        1,
        [
            {
                "skill_id": "51",
                "name": "risk-review",
                "description": "Published",
                "latest_published_version": {
                    "version": 2,
                    "sc_version_number": "2.0.0",
                    "published_at": published_at,
                },
            }
        ],
    )

    versions = client.get("/openapi/v1/bots/spaces/7/skills/51/versions")
    detail = client.get("/openapi/v1/bots/spaces/7/skills/51/versions/2")
    tree = client.get("/openapi/v1/bots/spaces/7/skills/51/versions/2/files")
    file = client.get("/openapi/v1/bots/spaces/7/skills/51/versions/2/files/SKILL.md")
    consumable = client.get("/openapi/v1/bots/spaces/7/skills/consumable")

    assert versions.json()["data"]["items"][0]["version"] == 2
    assert detail.json()["data"]["mcp_dependencies"] == ["mcp.a"]
    assert tree.json()["data"]["files"][0]["path"] == "SKILL.md"
    assert file.json()["data"]["content"] == "# Published"
    assert consumable.json()["data"]["items"][0]["skill_id"] == "51"


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
        scope=DomainSpaceListScope.ALL,
    )


def test_list_spaces_forwards_accessible_scope(client, space_service):
    space_service.list_spaces.return_value = (0, [])

    response = client.get("/openapi/v1/bots/spaces", params={"scope": "accessible"})

    assert response.status_code == 200
    assert (
        space_service.list_spaces.call_args.kwargs["scope"]
        is DomainSpaceListScope.ACCESSIBLE
    )


def test_list_spaces_rejects_unknown_scope(client):
    response = client.get("/openapi/v1/bots/spaces", params={"scope": "joined"})

    assert response.status_code == 422


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
