from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.api.publish_approval import ApprovalResult
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationConflictError,
    ServicePublicationLockedError,
    ServicePublicationNotFoundError,
    ServicePublicationUnsupportedError,
)
from agentclaw.community.core.service_bot.services.service_publication_facade import (
    ServicePublicationFacade,
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


@pytest.mark.parametrize(
    ("status", "product_status", "actions"),
    [
        (PublishStatus.DRAFT, "draft", ["publish_staging", "delete"]),
        (PublishStatus.BUILDING, "deploying", []),
        (PublishStatus.BUILT, "deploying", []),
        (PublishStatus.VALIDATE_PUB, "deploying", []),
        (
            PublishStatus.VALIDATING,
            "staging",
            ["publish_online", "restart_publish", "cancel_staging"],
        ),
        (PublishStatus.ONLINE_PUB, "deploying", []),
        (PublishStatus.SUCCESS, "running", ["restart_publish", "offline"]),
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
    assert result["available_actions"] == actions


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


def test_bot_absence_and_insufficient_permission_are_both_masked(deps):
    deps.bot_repo.get_by_id_and_owner.return_value = None
    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.list_publications("bot-1", actor_id="stranger", owner_id="owner")

    deps.bot_repo.get_by_id_and_owner.return_value = {
        "id": 10,
        "bot_id": "bot-1",
        "owner_id": "owner",
        "bot_type": "service",
    }
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.NONE
    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.list_publications("bot-1", actor_id="stranger", owner_id="owner")


def test_non_service_bot_is_rejected_from_publication_reads(deps):
    deps.bot_repo.get_by_id_and_owner.return_value["bot_type"] = "personal"
    with pytest.raises(ServicePublicationUnsupportedError):
        deps.facade.list_publications("bot-1", actor_id="owner", owner_id="owner")


def test_convert_requires_owner_and_rejects_local_or_existing_service(deps):
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.MEMBER
    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.convert_to_service("bot-1", actor_id="member", owner_id="owner")

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


def test_service_config_update_requires_owner_and_handles_legacy_ext(deps):
    deps.bot_repo.get_by_id_and_owner.return_value["ext"] = {
        "service_bot_config": {"should_approval": "false"}
    }
    assert (
        deps.facade.get_service_config("bot-1", actor_id="member", owner_id="owner")[
            "should_approval"
        ]
        is False
    )

    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.MEMBER
    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.update_service_config(
            "bot-1",
            actor_id="member",
            owner_id="owner",
            should_approval=True,
        )
    deps.bot_service.update_bot_ext.assert_not_called()


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


def test_delete_is_owner_only_and_delegates_domain_rule(deps, monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.service_bot.services.service_publication_facade.get_current_env",
        lambda: "dev",
    )
    row = record(1, PublishStatus.DRAFT)
    deps.publish_repo.list_by_source_bot.return_value = [row]
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.MEMBER
    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.delete_initial_draft("bot-1", actor_id="member", owner_id="owner")

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
    deps.lock_service.acquire_lock.return_value = lock
    deps.lock_service.steal_lock.return_value = lock
    deps.lock_service.release_lock.return_value = True

    assert (
        deps.facade.acquire_lock("bot-1", actor_id="member", owner_id="owner") is lock
    )
    assert deps.facade.release_lock("bot-1", actor_id="member", owner_id="owner")
    assert (
        deps.facade.get_lock("bot-1", actor_id="member", owner_id="owner")
        is deps.lock_service.get_lock_info.return_value
    )
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.ADMIN
    assert deps.facade.steal_lock("bot-1", actor_id="member", owner_id="owner") is lock


def test_steal_lock_requires_admin_or_owner(deps):
    deps.collaborator_service.get_permission_level.return_value = PermissionLevel.MEMBER

    with pytest.raises(ServicePublicationNotFoundError):
        deps.facade.steal_lock("bot-1", actor_id="member", owner_id="owner")

    deps.lock_service.steal_lock.assert_not_called()
