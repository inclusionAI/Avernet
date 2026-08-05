"""Endpoint tests for service-bot draft restore endpoints.

Covers:
- GET /api/service-bot/publish/{publish_id}/can-restore-draft
- POST /api/service-bot/publish/{publish_id}/restore-draft
- GET /api/service-bot/publish/{publish_id}/draft-restore-operations/{operation_id}
"""
from __future__ import annotations

import json

from agentclaw.community.api.bot_publish_service import BotPublishServiceProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishOperationKind,
    PublishOperationModel,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.core.service_bot.repository.publish_operation_repository import (
    PublishOperationRepository,
)
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_failing_method,
    bind_method,
    endpoint_test,
)

_OWNER = "u_draft_restore"
_BOT_ID = "draft_restore_bot"
_SOURCE_ID = 9101
_DRAFT_ID = 9102
_HEADERS = {"x-user-id": _OWNER}

_CAN_RESTORE = "/api/service-bot/publish/{publish_id}/can-restore-draft"
_RESTORE = "/api/service-bot/publish/{publish_id}/restore-draft"
_RESTORE_STATUS = (
    "/api/service-bot/publish/{publish_id}/"
    "draft-restore-operations/{operation_id}"
)
_STATUS_OPERATION_ID = 9201


def _insert_publish(
    world,
    *,
    publish_id: int,
    status: str,
    version: int,
    source_bot_pk: int = 1,
    last_pub_id: int = 0,
    ext: dict | None = None,
) -> None:
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        session.add(BotPublishModel(
            id=publish_id,
            source_bot_pk=source_bot_pk,
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
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER,
        entity_type="staff",
        device_id="BOT-draft-restore",
        device_provider="arca",
        env="dev",
        device_props={},
        status="ACTIVE",
        apply_reason="seed",
        applied_by=_OWNER,
    )
    bot = world.get(BotRepository).insert({
        "bot_id": _BOT_ID,
        "bot_name": "Draft Restore Bot",
        "owner_id": _OWNER,
        "owner_name": _OWNER,
        "bot_type": "service",
        "status": "ACTIVE",
        "entity_id": _OWNER,
        "entity_type": "staff",
        "creator_id": _OWNER,
        "active_engine": "openclaw",
        "binding_id": binding_id,
    })
    _insert_publish(
        world,
        publish_id=_SOURCE_ID,
        status=PublishStatus.UPGRADED,
        version=1,
        source_bot_pk=bot["id"],
        ext={"migration_path": "/artifacts/draft-restore/v1/openclaw"},
    )
    _insert_publish(
        world,
        publish_id=_DRAFT_ID,
        status=PublishStatus.DRAFT,
        version=2,
        source_bot_pk=bot["id"],
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
    """Pass authorization, then fail the lookup the way infrastructure would.

    The lookup itself is a plain read, so nothing in the request can make it
    fail; the failure goes in at the DI seam so the router's 500 envelope is
    what gets asserted.
    """
    _seed_restoreable_draft(world)
    bind_failing_method(
        world,
        BotPublishServiceProtocol,
        "can_restore_draft",
        RuntimeError("draft restore lookup failed"),
    )


async def _execute_restore_draft_stub(self, **kwargs):
    operation_id = kwargs["operation_id"]
    self._publish_operation_repo.update_result(
        operation_id,
        {
            "restore_type": "migration_path",
            "draft_binding_id": 802,
        },
    )
    self._publish_operation_repo.complete_without_workflow(operation_id)
    return {
        "status": "success",
        "restore_type": "migration_path",
        "draft_binding_id": 802,
    }


def _seed_restore_happy(world) -> None:
    """Stand in for the rsync-backed restore, keeping its ledger effects.

    ``execute_restore_draft`` copies a historical artifact tree over the NAS
    mount with ``sudo rsync`` — the one step of this flow no test host can
    perform. The stand-in writes the same operation-ledger rows the real
    implementation writes, through the service's own repository, so the
    endpoint's response and everything the assertions read back are produced
    by the real code around it.
    """
    from agentclaw.community.api.publish_flow_service import (
        PublishFlowServiceProtocol,
    )
    from agentclaw.community.core.service_bot.services.publish_flow_service import (
        PublishFlowService,
    )

    _seed_restoreable_draft(world)
    # The durable task handler receives the flow as its concrete class while the
    # router injects the Protocol, so both keys have to reach the stand-in.
    bind_method(
        world,
        PublishFlowService,
        "execute_restore_draft",
        _execute_restore_draft_stub,
        also_bind=(PublishFlowServiceProtocol,),
    )


def _assert_restore_completed(response, world) -> None:  # noqa: ARG001
    response_data = response.json()["data"]
    operation_id = response_data["operation_id"]
    operation = world.get(PublishOperationRepository).get_by_id(operation_id)
    assert operation is not None
    assert operation.publish_id == _DRAFT_ID
    assert operation.operation_kind == PublishOperationKind.DRAFT_RESTORE.value
    assert operation.stage == PublishStage.DRAFT.value
    assert operation.state == PublishOperationState.COMPLETED.value
    assert operation.params["source_publish_id"] == _SOURCE_ID
    assert operation.params["source_version"] == 1
    assert operation.result == {
        "restore_type": "migration_path",
        "draft_binding_id": 802,
    }

    record = world.get(BotPublishRepositoryProtocol).get_by_id(_DRAFT_ID)
    assert record is not None
    assert record.status == PublishStatus.DRAFT
    assert record.ext == {"existing": True}


def _seed_completed_restore_operation(world) -> None:
    _seed_restoreable_draft(world)
    db = world.get(DatabasePlugin)
    with db.orm_session() as session:
        session.add(PublishOperationModel(
            id=_STATUS_OPERATION_ID,
            publish_id=_DRAFT_ID,
            operation_kind=PublishOperationKind.DRAFT_RESTORE.value,
            stage=PublishStage.DRAFT.value,
            attempt=1,
            state=PublishOperationState.COMPLETED.value,
            request_id="pub_9102_draft_restore_draft_a1",
            operator=_OWNER,
            bot_uuid="BOT-draft-restore",
            params=json.dumps({
                "source_publish_id": _SOURCE_ID,
                "source_version": 1,
            }),
            result=json.dumps({
                "restore_type": "migration_path",
                "draft_binding_id": 802,
            }),
            env="dev",
        ))
        session.flush()


def _seed_restore_status_failure(world) -> None:
    """Take the operation ledger away underneath the status read."""
    _seed_restoreable_draft(world)
    bind_failing_method(
        world,
        BotPublishServiceProtocol,
        "get_draft_restore_status",
        RuntimeError("ledger unavailable"),
    )


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


@endpoint_test(
    method="GET",
    path=_RESTORE_STATUS,
    scenario="happy_completed_operation",
    input=CaseInput(
        path_params={
            "publish_id": _DRAFT_ID,
            "operation_id": _STATUS_OPERATION_ID,
        },
        headers=_HEADERS,
    ),
    seed=_seed_completed_restore_operation,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "message": "查询草稿恢复状态成功",
            "data": {
                "draft_publish_id": _DRAFT_ID,
                "operation_id": _STATUS_OPERATION_ID,
                "task_id": "pub_9102_draft_restore_draft_a1",
                "attempt": 1,
                "status": "success",
                "operation_state": PublishOperationState.COMPLETED.value,
                "source_publish_id": _SOURCE_ID,
                "source_version": 1,
                "restore_type": "migration_path",
                "draft_binding_id": 802,
                "error": None,
            },
        },
    ),
)
def get_draft_restore_status_happy():
    """A completed ledger attempt is exposed through the polling endpoint."""


@endpoint_test(
    method="GET",
    path=_RESTORE_STATUS,
    scenario="error_operation_not_found",
    input=CaseInput(
        path_params={
            "publish_id": _DRAFT_ID,
            "operation_id": _STATUS_OPERATION_ID,
        },
        headers=_HEADERS,
    ),
    seed=_seed_restoreable_draft,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 404,
            "message": (
                "草稿恢复操作不存在: "
                f"publish_id={_DRAFT_ID}, operation_id={_STATUS_OPERATION_ID}"
            ),
        },
    ),
)
def get_draft_restore_status_not_found():
    """An unknown operation id is not disclosed as another attempt."""


@endpoint_test(
    method="GET",
    path=_RESTORE_STATUS,
    scenario="error_service_failure",
    input=CaseInput(
        path_params={
            "publish_id": _DRAFT_ID,
            "operation_id": _STATUS_OPERATION_ID,
        },
        headers=_HEADERS,
    ),
    seed=_seed_restore_status_failure,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 500,
            "message": "查询草稿恢复状态失败: ledger unavailable",
        },
    ),
)
def get_draft_restore_status_error():
    """Unexpected ledger failures use the API error envelope."""
