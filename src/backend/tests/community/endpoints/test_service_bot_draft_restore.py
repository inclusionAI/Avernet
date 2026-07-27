"""Endpoint tests for service-bot draft restore endpoints.

Covers:
- GET /api/service-bot/publish/{publish_id}/can-restore-draft
- POST /api/service-bot/publish/{publish_id}/restore-draft
"""
from __future__ import annotations

import json
from unittest.mock import patch

from agentclaw.community.api.bot_publish_service import BotPublishServiceProtocol
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishStatus,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "u_draft_restore"
_BOT_ID = "draft_restore_bot"
_SOURCE_ID = 9101
_DRAFT_ID = 9102
_HEADERS = {"x-user-id": _OWNER}

_CAN_RESTORE = "/api/service-bot/publish/{publish_id}/can-restore-draft"
_RESTORE = "/api/service-bot/publish/{publish_id}/restore-draft"


def _insert_publish(
    world,
    *,
    publish_id: int,
    status: str,
    version: int,
    last_pub_id: int = 0,
    ext: dict | None = None,
) -> None:
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        session.add(BotPublishModel(
            id=publish_id,
            source_bot_pk=1,
            source_bot_id=_BOT_ID,
            publish_bot_id=_BOT_ID,
            name=f"Draft Restore V{version}",
            owner_id=_OWNER,
            permission_owner=_OWNER,
            status=status,
            version=version,
            last_pub_id=last_pub_id,
            env="dev",
            ext=json.dumps(ext or {}, ensure_ascii=False),
        ))
        session.flush()


def _seed_restoreable_draft(world) -> None:
    """Seed a V2 draft whose immediately previous V1 has a restore artifact."""
    make_staff_user(world, user_id=_OWNER)
    _insert_publish(
        world,
        publish_id=_SOURCE_ID,
        status=PublishStatus.UPGRADED,
        version=1,
        ext={"migration_path": "/artifacts/draft-restore/v1/openclaw"},
    )
    _insert_publish(
        world,
        publish_id=_DRAFT_ID,
        status=PublishStatus.DRAFT,
        version=2,
        last_pub_id=_SOURCE_ID,
        ext={"existing": True},
    )


def _seed_first_draft(world) -> None:
    """Seed a first-version draft, which has no historical artifact to restore."""
    make_staff_user(world, user_id=_OWNER)
    _insert_publish(
        world,
        publish_id=_DRAFT_ID,
        status=PublishStatus.DRAFT,
        version=1,
    )


def _seed_can_restore_failure(world) -> None:
    """Pass authorization, then force the service query seam to fail."""
    _seed_restoreable_draft(world)
    service = world.get(BotPublishServiceProtocol)
    patch.object(
        type(service),
        "can_restore_draft",
        side_effect=RuntimeError("draft restore lookup failed"),
    ).start()


async def _execute_restore_draft_stub(self, **_kwargs):  # noqa: ARG001
    return {
        "status": "success",
        "restore_type": "migration_path",
        "draft_binding_id": 802,
    }


def _seed_restore_happy(world) -> None:
    from agentclaw.community.core.service_bot.services.publish_flow_service import (
        PublishFlowService,
    )

    _seed_restoreable_draft(world)
    patch.object(
        PublishFlowService,
        "execute_restore_draft",
        new=_execute_restore_draft_stub,
    ).start()


def _assert_restore_completed(response, world) -> None:  # noqa: ARG001
    record = world.get(BotPublishRepositoryProtocol).get_by_id(_DRAFT_ID)
    assert record is not None
    assert record.status == PublishStatus.DRAFT
    restore_state = (record.ext or {}).get("draft_restore") or {}
    assert restore_state["status"] == "success"
    assert restore_state["source_publish_id"] == _SOURCE_ID
    assert restore_state["restore_type"] == "migration_path"
    assert record.ext["existing"] is True
    assert "migration_path" not in record.ext
    assert "binding" not in record.ext


@endpoint_test(
    method="GET",
    path=_CAN_RESTORE,
    scenario="happy_restoreable_draft",
    input=CaseInput(path_params={"publish_id": _DRAFT_ID}, headers=_HEADERS),
    seed=_seed_restoreable_draft,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "can_restore_draft": True,
                "restore_source": {
                    "source_publish_id": _SOURCE_ID,
                    "source_version": 1,
                },
            },
        },
    ),
)
def can_restore_draft_happy():
    """A DRAFT linked to a previous migration artifact is restoreable."""


@endpoint_test(
    method="GET",
    path=_CAN_RESTORE,
    scenario="error_service_failure",
    input=CaseInput(path_params={"publish_id": _DRAFT_ID}, headers=_HEADERS),
    seed=_seed_can_restore_failure,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def can_restore_draft_error():
    """Unexpected lookup failures are returned in the API error envelope."""


@endpoint_test(
    method="POST",
    path=_RESTORE,
    scenario="happy_restore_started",
    input=CaseInput(path_params={"publish_id": _DRAFT_ID}, headers=_HEADERS),
    seed=_seed_restore_happy,
    drain_background=True,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "message": "草稿恢复已启动",
            "data": {
                "draft_publish_id": _DRAFT_ID,
                "source_publish_id": _SOURCE_ID,
                "source_version": 1,
                "status": "restoring",
            },
        },
    ),
    extra_assertions=(_assert_restore_completed,),
)
def restore_draft_happy():
    """The endpoint starts restore and the drained background task completes."""


@endpoint_test(
    method="POST",
    path=_RESTORE,
    scenario="error_first_draft_has_no_history",
    input=CaseInput(path_params={"publish_id": _DRAFT_ID}, headers=_HEADERS),
    seed=_seed_first_draft,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 400,
            "message": "无法恢复草稿: 首次创建的草稿没有历史版本构造物",
        },
    ),
)
def restore_draft_error():
    """A first-version draft is rejected because no prior artifact exists."""
