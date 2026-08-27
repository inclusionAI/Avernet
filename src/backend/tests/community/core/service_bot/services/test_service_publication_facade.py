from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    Check,
)
from agentclaw.community.api.publish_approval import ApprovalResult
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.errors import (
    ServiceContainerConflictError,
    ServiceContainerNotFoundError,
    ServiceContainerUpstreamError,
    ServicePublicationConflictError,
    ServicePublicationLockedError,
    ServicePublicationNotFoundError,
    ServicePublicationUnsupportedError,
)
from agentclaw.community.core.devices.errors import (
    DeviceServiceError,
    InvalidDeviceStatusError,
)
from agentclaw.community.core.devices.services.device_instance_service import (
    BotPublishNotFoundError,
)
from agentclaw.community.core.service_bot.services.service_publication_facade import (
    ServicePublicationFacade,
)
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    PublishNotFoundError,
    PublishStatusInvalidError,
)


NOW = datetime(2026, 8, 17, 12, 0, 0)


def record(
    record_id: int,
    status: PublishStatus,
    *,
    version: int | None = None,
    source_bot_pk: int = 10,
    source_bot_id: str = "bot-1",
    env: str = "dev",
    ext: dict | None = None,
) -> BotPublishRecord:
    return BotPublishRecord(
        id=record_id,
        source_bot_pk=source_bot_pk,
        source_bot_id=source_bot_id,
        publish_bot_id=source_bot_id,
        name="Service Bot",
        owner_id="owner",
        status=status.value,
        version=version if version is not None else record_id,
        env=env,
        ext=ext,
        permission_owner="owner",
        gmt_create=NOW,
        gmt_modified=NOW,
    )


@pytest.fixture
def deps():
    values = SimpleNamespace(
        bot_repo=Mock(),
        publish_repo=Mock(),
        publish_service=Mock(),
        flow_service=Mock(),
        approval_service=Mock(),
        collaborator_service=Mock(),
        lock_service=Mock(),
        bot_service=Mock(),
        device_service=Mock(),
    )
    values.bot_repo.get_by_id_and_owner.return_value = {
        "id": 10,
        "bot_id": "bot-1",
        "owner_id": "owner",
        "bot_type": "service",
        "active_engine": "openclaw",
    }
    values.collaborator_service.get_permission_level.return_value = (
        PermissionLevel.OWNER
    )
    values.publish_repo.list_by_source_bot.return_value = []
    values.publish_service.can_upgrade_publish.return_value = True
    values.lock_service.get_lock_info.return_value = SimpleNamespace(
        lock=None,
        holder_name=None,
        has_collaborators=False,
        is_owner=False,
    )
    values.facade = ServicePublicationFacade(
        bot_repo=values.bot_repo,
        publish_repo=values.publish_repo,
        publish_service=values.publish_service,
        flow_service=values.flow_service,
        approval_service=values.approval_service,
        collaborator_service=values.collaborator_service,
        lock_service=values.lock_service,
        bot_service=values.bot_service,
        device_service=values.device_service,
    )
    return values


def test_list_projects_latest_two_and_deduplicates_released(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    rows = [
        record(1, PublishStatus.RELEASED, version=1),
        record(2, PublishStatus.RELEASED, version=2),
        record(3, PublishStatus.UPGRADED, version=3),
        record(4, PublishStatus.DRAFT, version=4),
        record(5, PublishStatus.SUCCESS, version=5),
    ]
    deps.publish_repo.list_by_source_bot.return_value = rows

    result = deps.facade.list_publications("bot-1", actor_id="owner", owner_id="owner")

    assert [item["publication_id"] for item in result["items"]] == [5, 4]
    assert result["items"][0]["status"] == "running"
    assert result["items"][0]["live_version"] == 5
    assert result["items"][0]["card_id"] == "service:bot-1:5"
    assert result["items"][1]["available_actions"] == ["publish_staging"]


def _container(health_status: str = "ABNORMAL") -> dict:
    return {
        "device_uuid": "DEVICE-001",
        "status": "FAILED",
        "health_status": health_status,
        "engine_type": "openclaw",
    }


def test_list_containers_authorizes_and_requests_realtime_health(deps):
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.MEMBER
    deps.device_service.get_instances_by_bot.return_value = {
        "bot_uuid": "runtime-1",
        "devices": [_container()],
    }

    result = deps.facade.list_containers("bot-1", actor_id="member", owner_id="owner")

    assert result == {"bot_id": "bot-1", "instances": [_container()]}
    deps.device_service.get_instances_by_bot.assert_called_once_with(
        bot_id="bot-1", health_check=True
    )


def test_list_containers_masks_provider_failure(deps):
    deps.device_service.get_instances_by_bot.side_effect = DeviceServiceError("secret")

    with pytest.raises(ServiceContainerUpstreamError):
        deps.facade.list_containers("bot-1", actor_id="owner", owner_id="owner")


def test_list_containers_reports_missing_live_runtime_as_conflict(deps):
    deps.device_service.get_instances_by_bot.side_effect = BotPublishNotFoundError(
        "missing"
    )

    with pytest.raises(ServiceContainerConflictError):
        deps.facade.list_containers("bot-1", actor_id="owner", owner_id="owner")


def test_restart_container_accepts_owned_abnormal_instance(deps):
    deps.device_service.get_instances_by_bot.return_value = {
        "devices": [_container()]
    }
    deps.device_service.restart_device_by_bot.return_value = {"publish_id": 42}

    result = deps.facade.restart_container(
        "bot-1",
        "DEVICE-001",
        actor_id="owner",
        owner_id="owner",
    )

    assert result == {
        "bot_id": "bot-1",
        "instance_id": "DEVICE-001",
        "publish_id": 42,
        "accepted": True,
    }
    call = deps.device_service.restart_device_by_bot.call_args
    assert call.kwargs["bot_id"] == "bot-1"
    assert call.kwargs["device_uuid"] == "DEVICE-001"
    assert call.kwargs["operator"].staff_id == "owner"


def test_restart_container_refuses_instance_from_another_bot(deps):
    deps.device_service.get_instances_by_bot.return_value = {"devices": [_container()]}

    with pytest.raises(ServiceContainerNotFoundError):
        deps.facade.restart_container(
            "bot-1",
            "DEVICE-OTHER",
            actor_id="owner",
            owner_id="owner",
        )

    deps.device_service.restart_device_by_bot.assert_not_called()


def test_restart_container_refuses_healthy_instance(deps):
    deps.device_service.get_instances_by_bot.return_value = {
        "devices": [_container("ACTIVE")]
    }

    with pytest.raises(ServiceContainerConflictError):
        deps.facade.restart_container(
            "bot-1",
            "DEVICE-001",
            actor_id="owner",
            owner_id="owner",
        )


def test_restart_container_still_carries_the_owner_bar():
    """The bar this facade enforced, asserted where it is enforced now.

    ``_resolve_bot`` took ``required_level`` and refused below it; the four
    OWNER operations passed ``PermissionLevel.OWNER`` explicitly. That
    parameter is gone — every route reaching the facade declares its bar as a
    ``Check`` row and ``bot_access`` enforces it before the handler runs, with
    the same masked answer a missing bot gets.

    So a MEMBER can no longer be refused *here*, and asserting they are would
    be asserting against a facade that does not exist. What is still this
    group's to state is that the bar did not quietly become MEMBER on the way
    across — hence the row, read back.
    """
    key = ("POST", "/openapi/v1/bots/{bot_id}/containers/{instance_id}/restart")
    rule = AUTHORIZATION[key]

    assert isinstance(rule, Check), "container restart is no longer adjudicated"
    assert rule.level is PermissionLevel.OWNER, (
        "container restart moved off OWNER, which _resolve_bot enforced before "
        "the seam took it over"
    )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (BotPublishNotFoundError("missing"), ServiceContainerConflictError),
        (InvalidDeviceStatusError("mismatch"), ServiceContainerNotFoundError),
        (DeviceServiceError("upstream"), ServiceContainerUpstreamError),
    ],
)
def test_restart_container_normalizes_runtime_failures(deps, failure, expected):
    deps.device_service.get_instances_by_bot.return_value = {"devices": [_container()]}
    deps.device_service.restart_device_by_bot.side_effect = failure

    with pytest.raises(expected):
        deps.facade.restart_container(
            "bot-1",
            "DEVICE-001",
            actor_id="owner",
            owner_id="owner",
        )


@pytest.mark.parametrize(
    ("status", "product_status", "actions"),
    [
        (PublishStatus.DRAFT, "draft", ["publish_staging", "delete"]),
        (PublishStatus.BUILDING, "deploying", []),
        (PublishStatus.BUILT, "deploying", []),
        (PublishStatus.VALIDATE_PUB, "deploying", []),
        (
            PublishStatus.VALIDATING,
            "prestable",
            ["publish_online", "restart_publish", "cancel_staging"],
        ),
        (PublishStatus.ONLINE_PUB, "deploying", []),
        (
            PublishStatus.SUCCESS,
            "running",
            ["upgrade", "restart_publish", "offline"],
        ),
        (PublishStatus.RELEASED, "offline", []),
        (PublishStatus.FAILED, "deploying", ["retry"]),
    ],
)
def test_projection_status_and_actions(
    deps, monkeypatch, status, product_status, actions
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(1, status)
    deps.publish_repo.get_by_id.return_value = row
    deps.publish_repo.list_by_source_bot.return_value = [row]

    result = deps.facade.get_publication("bot-1", 1, actor_id="owner", owner_id="owner")

    assert result["status"] == product_status
    assert result["internal_status"] == status.value
    assert result["available_actions"] == actions


@pytest.mark.parametrize(
    "historical_status",
    [PublishStatus.UPGRADED, PublishStatus.RELEASED],
)
def test_draft_delete_remains_available_after_inactive_publish_history(
    deps, historical_status
):
    draft = record(2, PublishStatus.DRAFT, version=2)
    historical = record(1, historical_status, version=1)

    assert deps.facade._actions(
        draft,
        level=PermissionLevel.OWNER,
        all_records=[draft, historical],
    ) == ["publish_staging", "delete"]


def test_draft_delete_is_blocked_while_an_online_version_exists(deps):
    draft = record(2, PublishStatus.DRAFT, version=2)
    online = record(1, PublishStatus.SUCCESS, version=1)

    assert deps.facade._actions(
        draft,
        level=PermissionLevel.OWNER,
        all_records=[draft, online],
    ) == ["publish_staging"]


def test_running_upgrade_is_hidden_when_a_non_failed_successor_exists(deps):
    online = record(1, PublishStatus.SUCCESS, version=1)
    successor = record(2, PublishStatus.DRAFT, version=2)
    successor.last_pub_id = online.id

    assert deps.facade._actions(
        online,
        level=PermissionLevel.ADMIN,
        all_records=[online, successor],
    ) == ["restart_publish", "offline"]


def test_running_upgrade_remains_available_after_a_failed_successor(deps):
    online = record(1, PublishStatus.SUCCESS, version=1)
    failed = record(2, PublishStatus.FAILED, version=2)
    failed.last_pub_id = online.id

    assert deps.facade._actions(
        online,
        level=PermissionLevel.ADMIN,
        all_records=[online, failed],
    ) == ["upgrade", "restart_publish", "offline"]


def test_running_upgrade_is_hidden_from_members(deps):
    online = record(1, PublishStatus.SUCCESS, version=1)

    assert deps.facade._actions(
        online,
        level=PermissionLevel.MEMBER,
        all_records=[online],
    ) == ["restart_publish", "offline"]


def test_upgrade_publication_creates_and_projects_the_next_draft(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    online = record(1, PublishStatus.SUCCESS, version=1)
    created = record(2, PublishStatus.DRAFT, version=2)
    created.last_pub_id = online.id
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.ADMIN
    deps.publish_repo.get_by_id.side_effect = [online, created]
    deps.publish_repo.list_by_source_bot.side_effect = [[online], [online, created]]
    deps.publish_service.upgrade_publish.return_value = created

    result = deps.facade.upgrade_publication(
        "bot-1",
        online.id,
        actor_id="admin",
        owner_id="owner",
    )

    assert result["publication_id"] == created.id
    assert result["version"] == 2
    assert result["status"] == "draft"
    deps.publish_service.upgrade_publish.assert_called_once_with(
        publish_id=online.id,
        owner_id="owner",
    )


def test_upgrade_publication_rejects_a_publication_from_another_bot(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    deps.publish_repo.get_by_id.return_value = record(
        1,
        PublishStatus.SUCCESS,
        source_bot_pk=99,
    )

    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.upgrade_publication(
            "bot-1",
            1,
            actor_id="admin",
            owner_id="owner",
        )

    deps.publish_service.upgrade_publish.assert_not_called()


def test_upgrade_publication_rejects_member_permission(deps):
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.MEMBER

    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.upgrade_publication(
            "bot-1",
            1,
            actor_id="member",
            owner_id="owner",
        )

    deps.publish_repo.get_by_id.assert_not_called()
    deps.publish_service.upgrade_publish.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [
        PublishStatus.DRAFT,
        PublishStatus.VALIDATING,
        PublishStatus.RELEASED,
        PublishStatus.FAILED,
    ],
)
def test_upgrade_publication_requires_a_running_source(deps, monkeypatch, status):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    source = record(1, status, version=1)
    deps.publish_repo.get_by_id.return_value = source
    deps.publish_repo.list_by_source_bot.return_value = [source]

    with pytest.raises(ServicePublicationConflictError):
        deps.facade.upgrade_publication(
            "bot-1",
            source.id,
            actor_id="admin",
            owner_id="owner",
        )

    deps.publish_service.upgrade_publish.assert_not_called()


def test_upgrade_publication_rejects_when_a_non_failed_successor_exists(
    deps, monkeypatch
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    online = record(1, PublishStatus.SUCCESS, version=1)
    successor = record(2, PublishStatus.DRAFT, version=2)
    successor.last_pub_id = online.id
    deps.publish_repo.get_by_id.return_value = online
    deps.publish_repo.list_by_source_bot.return_value = [online, successor]
    deps.publish_service.can_upgrade_publish.return_value = False

    with pytest.raises(ServicePublicationConflictError):
        deps.facade.upgrade_publication(
            "bot-1",
            online.id,
            actor_id="admin",
            owner_id="owner",
        )

    deps.publish_service.upgrade_publish.assert_not_called()


def test_upgrade_publication_preserves_legacy_failed_successor_retry(
    deps, monkeypatch
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    online = record(1, PublishStatus.SUCCESS, version=1)
    failed = record(2, PublishStatus.FAILED, version=2)
    failed.last_pub_id = online.id
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.ADMIN
    deps.publish_repo.get_by_id.side_effect = [online, failed]
    deps.publish_repo.list_by_source_bot.side_effect = [
        [online, failed],
        [online, failed],
    ]
    deps.publish_service.upgrade_publish.return_value = failed

    result = deps.facade.upgrade_publication(
        "bot-1",
        online.id,
        actor_id="admin",
        owner_id="owner",
    )

    assert result["publication_id"] == failed.id
    assert result["internal_status"] == PublishStatus.FAILED.value
    assert result["available_actions"] == ["retry"]
    deps.publish_service.upgrade_publish.assert_called_once_with(
        publish_id=online.id,
        owner_id="owner",
    )


@pytest.mark.parametrize(
    "failure",
    [PublishNotFoundError("gone"), PublishStatusInvalidError("changed")],
)
def test_upgrade_publication_normalizes_concurrent_state_changes(
    deps, monkeypatch, failure
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    online = record(1, PublishStatus.SUCCESS, version=1)
    deps.publish_repo.get_by_id.return_value = online
    deps.publish_repo.list_by_source_bot.return_value = [online]
    deps.publish_service.upgrade_publish.side_effect = failure

    with pytest.raises(ServicePublicationConflictError):
        deps.facade.upgrade_publication(
            "bot-1",
            online.id,
            actor_id="admin",
            owner_id="owner",
        )


def test_version_upgrade_keeps_the_legacy_admin_authorization_bar():
    key = (
        "POST",
        "/openapi/v1/bots/{bot_id}/lifecycle/{publication_id}/upgrade",
    )
    rule = AUTHORIZATION[key]

    assert isinstance(rule, Check)
    assert rule.level is PermissionLevel.ADMIN


def test_failed_projection_sanitizes_error_and_uses_source_status(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(
        1,
        PublishStatus.FAILED,
        ext={
            "source_status": PublishStatus.ONLINE_PUB.value,
            "error_message": "token=secret\ninternal stack",
            "approval": {
                "status": "PROCESSING",
                "puid": "approval-1",
                "approval_url": "https://approval.example.test/1",
            },
        },
    )
    deps.publish_repo.get_by_id.return_value = row
    deps.publish_repo.list_by_source_bot.return_value = [row]

    result = deps.facade.get_publication("bot-1", 1, actor_id="owner", owner_id="owner")

    assert result["deployment"]["action"] == "publish_online"
    assert "secret" not in result["deployment"]["error_message"]
    assert result["approval"]["approval_id"] == "approval-1"


@pytest.mark.parametrize(
    "publication",
    [
        None,
        record(1, PublishStatus.DRAFT, source_bot_pk=99),
        record(1, PublishStatus.DRAFT, source_bot_id="other"),
        record(1, PublishStatus.DRAFT, env="prod"),
    ],
)
def test_publication_must_belong_to_resolved_bot_and_env(
    deps, monkeypatch, publication
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    deps.publish_repo.get_by_id.return_value = publication

    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.get_publication("bot-1", 1, actor_id="member", owner_id="owner")


def test_a_bot_absent_under_the_addressed_owner_is_still_not_found(deps):
    """Half of what this used to assert; the other half moved.

    It drove both masked answers — a bot that is not there, and a caller below
    the bar — and asserted they were the same. The second is the seam's now,
    and ``bot_access`` answers it with a 404 byte-identical to an absent bot's,
    which is the same guarantee stated one layer up (``test_bot_access.py``).

    The owner-scoped resolve stays here, and stays a not-found: it is what
    keeps a bot id from resolving under an owner it does not belong to, and no
    route-level check replaces it.
    """
    deps.bot_repo.get_by_id_and_owner.return_value = None

    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.list_publications("bot-1", actor_id="stranger", owner_id="owner")


def test_non_service_bot_is_rejected_from_publication_reads(deps):
    deps.bot_repo.get_by_id_and_owner.return_value["bot_type"] = "personal"
    with pytest.raises(ServicePublicationUnsupportedError):
        deps.facade.list_publications("bot-1", actor_id="owner", owner_id="owner")


def test_convert_rejects_local_or_existing_service(deps):
    """The OWNER bar it also asserted is on the row now — see
    ``test_the_four_owner_operations_kept_their_bar``. What is left is the
    domain refusals, which never were authorization."""
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.OWNER
    with pytest.raises(ServicePublicationConflictError):
        deps.facade.convert_to_service("bot-1", actor_id="owner", owner_id="owner")

    deps.bot_repo.get_by_id_and_owner.return_value.update(
        bot_type="personal", active_engine="aicoding"
    )
    with pytest.raises(ServicePublicationUnsupportedError):
        deps.facade.convert_to_service("bot-1", actor_id="owner", owner_id="owner")

    deps.bot_repo.get_by_id_and_owner.return_value.update(
        bot_type="personal", active_engine="hermes"
    )
    with pytest.raises(ServicePublicationUnsupportedError):
        deps.facade.convert_to_service("bot-1", actor_id="owner", owner_id="owner")


def test_convert_returns_created_publication(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    personal = {
        **deps.bot_repo.get_by_id_and_owner.return_value,
        "bot_type": "personal",
    }
    service = {**personal, "bot_type": "service"}
    deps.bot_repo.get_by_id_and_owner.side_effect = [personal, service]
    created = record(7, PublishStatus.DRAFT)
    deps.publish_service.upgrade_bot_to_service.return_value = {
        "publish_record": created
    }
    deps.publish_repo.get_by_id.return_value = created
    deps.publish_repo.list_by_source_bot.return_value = [created]

    result = deps.facade.convert_to_service("bot-1", actor_id="owner", owner_id="owner")

    assert result["publication_id"] == 7
    deps.publish_service.upgrade_bot_to_service.assert_called_once_with(
        bot_id="bot-1", owner_id="owner"
    )


def test_convert_rejects_missing_publication(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    personal = {
        **deps.bot_repo.get_by_id_and_owner.return_value,
        "bot_type": "personal",
    }
    deps.bot_repo.get_by_id_and_owner.return_value = personal
    deps.publish_service.upgrade_bot_to_service.return_value = {}
    deps.publish_repo.list_by_source_bot.return_value = []

    with pytest.raises(ServicePublicationConflictError):
        deps.facade.convert_to_service("bot-1", actor_id="owner", owner_id="owner")


def test_service_config_read_and_owner_update_preserve_other_fields(deps):
    deps.bot_repo.get_by_id_and_owner.return_value["ext"] = {
        "service_bot_config": {"device_count": 2, "should_approval": False}
    }

    assert deps.facade.get_service_config(
        "bot-1", actor_id="member", owner_id="owner"
    ) == {"bot_id": "bot-1", "should_approval": False}

    updated = deps.facade.update_service_config(
        "bot-1",
        actor_id="owner",
        owner_id="owner",
        should_approval=True,
    )

    assert updated == {"bot_id": "bot-1", "should_approval": True}
    deps.bot_service.update_bot_ext.assert_called_once_with(
        "bot-1",
        "owner",
        {
            "service_bot_config": {
                "device_count": 2,
                "should_approval": True,
            }
        },
    )


def test_service_config_read_handles_legacy_ext(deps):
    deps.bot_repo.get_by_id_and_owner.return_value["ext"] = {
        "service_bot_config": {"should_approval": "false"}
    }
    assert (
        deps.facade.get_service_config("bot-1", actor_id="member", owner_id="owner")[
            "should_approval"
        ]
        is False
    )

    # The OWNER bar this also drove is on the row now — see
    # ``test_the_four_owner_operations_kept_their_bar``.


@pytest.mark.asyncio
async def test_staging_requires_collaborative_lock(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(1, PublishStatus.DRAFT)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.lock_service.get_lock_info.return_value = SimpleNamespace(
        has_collaborators=True,
        lock=SimpleNamespace(holder_user_id="other"),
    )

    with pytest.raises(ServicePublicationLockedError):
        await deps.facade.advance(
            "bot-1", "staging", actor_id="owner", owner_id="owner"
        )
    deps.flow_service.process.assert_not_called()


@pytest.mark.asyncio
async def test_staging_runs_when_actor_holds_lock(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(1, PublishStatus.DRAFT)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.lock_service.get_lock_info.return_value = SimpleNamespace(
        has_collaborators=True,
        lock=SimpleNamespace(holder_user_id="member"),
    )
    deps.flow_service.process = AsyncMock(
        return_value=SimpleNamespace(model_dump=lambda: {"status": "building"})
    )

    result = await deps.facade.advance(
        "bot-1", "staging", actor_id="member", owner_id="owner"
    )

    assert result["action"] == "publish_staging"
    assert result["operation_status"] == "pending"


@pytest.mark.asyncio
async def test_prestable_stage_alias_runs_the_existing_publish_flow(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(1, PublishStatus.DRAFT)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.lock_service.get_lock_info.return_value = SimpleNamespace(
        has_collaborators=False,
        lock=None,
    )
    deps.flow_service.process = AsyncMock(
        return_value=SimpleNamespace(model_dump=lambda: {"status": "building"})
    )

    result = await deps.facade.advance(
        "bot-1", "prestable", actor_id="owner", owner_id="owner"
    )

    assert result["action"] == "publish_staging"
    deps.flow_service.process.assert_awaited_once_with(1, "owner")


@pytest.mark.asyncio
async def test_online_returns_existing_approval_without_starting_flow(
    deps, monkeypatch
):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(2, PublishStatus.VALIDATING)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.approval_service.check_and_process_should_approval = AsyncMock(
        return_value=ApprovalResult(
            should_approval=True,
            status="PROCESSING",
            approval={"puid": "p-1", "approval_url": "https://example.test/p-1"},
            message="waiting",
        )
    )

    result = await deps.facade.advance(
        "bot-1", "online", actor_id="member", owner_id="owner"
    )

    assert result["operation_status"] == "waiting_approval"
    assert result["approval"]["approval_id"] == "p-1"
    deps.flow_service.process.assert_not_called()


@pytest.mark.asyncio
async def test_online_owner_skips_approval_and_starts_flow(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(2, PublishStatus.VALIDATING)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.approval_service.check_and_process_should_approval = AsyncMock(
        return_value=ApprovalResult(False, "SKIP", None, "owner")
    )
    deps.flow_service.process = AsyncMock(
        return_value=SimpleNamespace(model_dump=lambda: {"status": "online_pub"})
    )

    result = await deps.facade.advance(
        "bot-1", "online", actor_id="owner", owner_id="owner"
    )

    assert result["operation_status"] == "pending"
    deps.flow_service.process.assert_awaited_once_with(2, "owner")


def test_restart_validates_status_and_domain_acceptance(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    deps.publish_repo.list_by_source_bot.return_value = []
    with pytest.raises(ServicePublicationConflictError):
        deps.facade.restart("bot-1", "online", actor_id="owner", owner_id="owner")

    deps.publish_repo.list_by_source_bot.return_value = [
        record(1, PublishStatus.SUCCESS)
    ]
    deps.flow_service.restart_bot.return_value = {"success": False}
    with pytest.raises(ServicePublicationConflictError):
        deps.facade.restart("bot-1", "online", actor_id="owner", owner_id="owner")

    deps.flow_service.restart_bot.return_value = {"success": True}
    result = deps.facade.restart("bot-1", "online", actor_id="owner", owner_id="owner")
    assert result["action"] == "restart_publish"


@pytest.mark.asyncio
async def test_cancel_staging_and_retry_delegate_to_domain(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    validating = record(2, PublishStatus.VALIDATING)
    deps.publish_repo.list_by_source_bot.return_value = [validating]
    deps.publish_service.offline_publish = AsyncMock(return_value={"success": True})

    cancelled = await deps.facade.cancel_staging(
        "bot-1", actor_id="member", owner_id="owner"
    )
    assert cancelled["action"] == "cancel_staging"

    failed = record(2, PublishStatus.FAILED)
    deps.publish_repo.list_by_source_bot.return_value = [failed]
    deps.flow_service.retry = AsyncMock(
        return_value=SimpleNamespace(model_dump=lambda: {"status": "building"})
    )
    retried = await deps.facade.retry("bot-1", actor_id="member", owner_id="owner")
    assert retried["action"] == "retry"


@pytest.mark.asyncio
async def test_offline_honors_approval_then_executes_when_skipped(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(3, PublishStatus.SUCCESS)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.approval_service.check_and_process_offline_approval = AsyncMock(
        return_value=ApprovalResult(True, "PROCESSING", {"puid": "p-2"}, "wait")
    )
    deps.publish_service.offline_publish = AsyncMock(return_value={"success": True})

    waiting = await deps.facade.offline("bot-1", actor_id="member", owner_id="owner")
    assert waiting["operation_status"] == "waiting_approval"
    deps.publish_service.offline_publish.assert_not_awaited()

    deps.approval_service.check_and_process_offline_approval.return_value = (
        ApprovalResult(False, "SKIP", None, "owner")
    )
    executed = await deps.facade.offline("bot-1", actor_id="owner", owner_id="owner")
    assert executed["operation_status"] == "pending"
    deps.publish_service.offline_publish.assert_awaited_once_with(3)


def test_delete_delegates_the_domain_rule(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(1, PublishStatus.DRAFT)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    # The OWNER bar this also drove is on the row now — see
    # ``test_the_four_owner_operations_kept_their_bar``.
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.OWNER
    deps.publish_service.can_delete_bot.return_value = False
    with pytest.raises(ServicePublicationConflictError):
        deps.facade.delete_initial_draft("bot-1", actor_id="owner", owner_id="owner")

    deps.publish_service.can_delete_bot.return_value = True
    deps.publish_service.delete_service_bot.return_value = False
    with pytest.raises(ServicePublicationConflictError):
        deps.facade.delete_initial_draft("bot-1", actor_id="owner", owner_id="owner")

    deps.publish_service.delete_service_bot.return_value = True
    assert deps.facade.delete_initial_draft("bot-1", actor_id="owner", owner_id="owner")


def test_lock_operations_require_membership_and_delegate(deps):
    lock = SimpleNamespace(holder_user_id="member")
    deps.lock_service.get_lock_info.return_value = SimpleNamespace(
        lock=None,
        holder_name=None,
        has_collaborators=True,
        is_owner=False,
    )
    deps.publish_repo.list_by_source_bot.return_value = [record(1, PublishStatus.DRAFT)]
    deps.lock_service.acquire_lock.return_value = lock
    deps.lock_service.steal_lock.return_value = lock
    deps.lock_service.release_lock.return_value = True

    assert (
        deps.facade.acquire_lock("bot-1", actor_id="member", owner_id="owner") is lock
    )
    assert deps.facade.release_lock("bot-1", actor_id="member", owner_id="owner")
    info = deps.facade.get_lock("bot-1", actor_id="member", owner_id="owner")
    assert info.has_collaborators is True
    assert info.need_lock is True
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.MEMBER
    assert deps.facade.steal_lock("bot-1", actor_id="member", owner_id="owner") is lock


def test_lock_is_not_created_without_collaborators(deps):
    assert deps.facade.acquire_lock("bot-1", actor_id="owner", owner_id="owner") is None
    assert deps.facade.steal_lock("bot-1", actor_id="owner", owner_id="owner") is None
    deps.lock_service.acquire_lock.assert_not_called()
    deps.lock_service.steal_lock.assert_not_called()


def test_lock_projection_requires_lock_for_any_collaborative_write(deps):
    deps.lock_service.get_lock_info.return_value = SimpleNamespace(
        lock=None,
        holder_name=None,
        has_collaborators=True,
        is_owner=False,
    )
    deps.publish_repo.list_by_source_bot.return_value = [
        record(1, PublishStatus.SUCCESS)
    ]

    info = deps.facade.get_lock("bot-1", actor_id="member", owner_id="owner")

    assert info.has_collaborators is True
    assert info.need_lock is True


def test_lock_takeover_still_requires_bot_membership():
    """Takeover is the forceful operation, so its bar is worth stating twice.

    The lock service assumes authorization already happened, and PD grants
    takeover to every Bot member — which is why ``steal_lock`` used to pass
    ``required_level=PermissionLevel.MEMBER`` explicitly, with a COSEC comment
    saying so. The parameter is gone; the comment moved to the call site and
    points here, and the bar itself is the row.
    """
    rule = AUTHORIZATION[("POST", "/openapi/v1/bots/{bot_id}/edit-lock/steal")]

    assert isinstance(rule, Check), "lock takeover is no longer adjudicated"
    assert rule.level is PermissionLevel.MEMBER


def test_the_facade_itself_refuses_below_the_bar(deps, monkeypatch):
    """The Service API contract promises to authorize, so drive it directly.

    ``ServicePublicationFacadeProtocol``'s docstring says *"Resolve, authorize
    and orchestrate"*. This PR briefly deleted that refusal on the reasoning
    that ``bot_access`` adjudicates first — true for ``/openapi/v1``, and beside
    the point for a contract, which promises future callers too. A P2 in round 4
    caught the divergence; this is what keeps it caught.

    Driven through the facade with **no seam in front of it**, which is exactly
    the position a background consumer or a second adapter would be in. The row
    assertions in the sibling test cover the other half — that the seam declares
    the same bars — and neither test can substitute for the other.
    """
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services."
        "service_publication_facade.get_current_env",
        lambda: "dev",
    )
    deps.publish_repo.list_by_source_bot.return_value = [record(1, PublishStatus.DRAFT)]

    # An ADMIN collaborator is below OWNER, so every owner-only operation is a
    # masked not-found — not a 403, which would confirm the bot exists.
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.ADMIN
    for label, call in (
        ("restart_container", lambda: deps.facade.restart_container(
            "bot-1", "DEVICE-001", actor_id="admin", owner_id="owner")),
        ("convert_to_service", lambda: deps.facade.convert_to_service(
            "bot-1", actor_id="admin", owner_id="owner")),
        ("update_service_config", lambda: deps.facade.update_service_config(
            "bot-1", actor_id="admin", owner_id="owner", should_approval=True)),
        ("delete_initial_draft", lambda: deps.facade.delete_initial_draft(
            "bot-1", actor_id="admin", owner_id="owner")),
    ):
        with pytest.raises(ServicePublicationNotFoundError):
            call()
        assert True, label

    # And a caller with no relation at all is refused the MEMBER-barred read.
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.NONE
    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.list_publications("bot-1", actor_id="stranger", owner_id="owner")


def test_the_four_owner_operations_kept_their_bar():
    """The four that were OWNER, and the twelve that were MEMBER, still are.

    ``_resolve_bot``'s ``required_level`` was the only record of which
    operation sat at which bar, and deleting it would have left that record
    nowhere if the rows had not picked it up. This reads all sixteen back.
    """
    owner_only = {
        ("POST", "/openapi/v1/bots/{bot_id}/containers/{instance_id}/restart"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/lifecycle"),
        ("PUT", "/openapi/v1/bots/{bot_id}/lifecycle/approval"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/upgrade"),
    }
    member = {
        ("GET", "/openapi/v1/bots/{bot_id}/containers"),
        ("GET", "/openapi/v1/bots/{bot_id}/lifecycle"),
        ("GET", "/openapi/v1/bots/{bot_id}/lifecycle/approval"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/advance"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/cancel-staging"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/offline"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/restart"),
        ("POST", "/openapi/v1/bots/{bot_id}/lifecycle/retry"),
        ("GET", "/openapi/v1/bots/{bot_id}/edit-lock"),
        ("POST", "/openapi/v1/bots/{bot_id}/edit-lock"),
        ("DELETE", "/openapi/v1/bots/{bot_id}/edit-lock"),
        ("POST", "/openapi/v1/bots/{bot_id}/edit-lock/steal"),
    }
    assert len(owner_only) + len(member) == 16

    for key in owner_only:
        rule = AUTHORIZATION[key]
        assert isinstance(rule, Check) and rule.level is PermissionLevel.OWNER, (
            f"{key[0]} {key[1]} was OWNER in _resolve_bot and is not now"
        )
    for key in member:
        rule = AUTHORIZATION[key]
        assert isinstance(rule, Check) and rule.level is PermissionLevel.MEMBER, (
            f"{key[0]} {key[1]} was MEMBER in _resolve_bot and is not now"
        )


def test_lock_can_be_acquired_without_an_editable_draft(deps):
    deps.lock_service.get_lock_info.return_value = SimpleNamespace(
        lock=None,
        holder_name=None,
        has_collaborators=True,
        is_owner=False,
    )
    deps.publish_repo.list_by_source_bot.return_value = [
        record(1, PublishStatus.SUCCESS)
    ]

    deps.lock_service.acquire_lock.return_value = SimpleNamespace(
        holder_user_id="member"
    )

    result = deps.facade.acquire_lock("bot-1", actor_id="member", owner_id="owner")

    assert result.holder_user_id == "member"
    deps.lock_service.acquire_lock.assert_called_once_with("bot-1", "owner", "member")
