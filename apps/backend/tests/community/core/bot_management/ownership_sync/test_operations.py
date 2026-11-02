"""Operations repair requires trusted admin and never accepts Owner overrides."""

from unittest.mock import Mock

import pytest

from agentclaw.community.core.bot_management.ownership_sync.operations import (
    OwnershipRepairError,
    OwnershipSyncOperations,
)
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncKey
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


@pytest.fixture
def ops():
    bots, repair = Mock(), Mock()
    bots.get_by_pk.return_value = {
        "id": 7,
        "bot_id": "bot",
        "bot_type": "service",
        "env": "pre",
        "entity_id": "A",
        "owner_id": "B",
        "ext": {},
    }
    repair.request_bot.return_value = SubmissionResult.QUEUED
    return (
        OwnershipSyncOperations(bots, repair, frozenset({"ops"}), "pre"),
        bots,
        repair,
    )


def test_only_admin_can_request(ops):
    service, bots, repair = ops
    with pytest.raises(OwnershipRepairError, match="ADMIN_REQUIRED"):
        service.request_repair(bot_pk=7, actor_id="B")
    bots.get_by_pk.assert_not_called()
    repair.request_bot.assert_not_called()


def test_scoped_admission_is_not_confirmation(ops, caplog):
    service, bots, repair = ops
    with avernet_tenant_scope("t"), caplog.at_level("INFO"):
        result = service.request_repair(bot_pk=7, actor_id="ops")
    repair.request_bot.assert_called_once_with(SyncKey("t", "pre", 7))
    assert result == {
        "bot_pk": 7,
        "bcn_bot_id": "bot:A",
        "submission": "queued",
        "delivery_confirmed": False,
    }
    assert "actor_id=ops" in caplog.text


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"bot_type": "personal"}, "SERVICE_BOT_REQUIRED"),
        ({"bot_id": "default"}, "SERVICE_BOT_REQUIRED"),
        ({"env": "prod"}, "BOT_NOT_FOUND"),
        ({"is_delete": 1}, "BOT_NOT_FOUND"),
    ],
)
def test_ineligible_bot_is_not_submitted(ops, patch, code):
    service, bots, repair = ops
    bots.get_by_pk.return_value.update(patch)
    with pytest.raises(OwnershipRepairError, match=code):
        service.request_repair(bot_pk=7, actor_id="ops")
    repair.request_bot.assert_not_called()


@pytest.mark.parametrize(
    "result", [SubmissionResult.DISABLED, SubmissionResult.REJECTED]
)
def test_rejected_submission_is_not_success(ops, result):
    service, bots, repair = ops
    repair.request_bot.return_value = result
    with pytest.raises(OwnershipRepairError, match="OWNERSHIP_SYNC_UNAVAILABLE"):
        service.request_repair(bot_pk=7, actor_id="ops")


def test_invalid_address_cannot_enqueue(ops):
    service, bots, repair = ops
    bots.get_by_pk.return_value["ext"] = {"bcn_provider_bot_ref": "different:bot"}
    with pytest.raises(OwnershipRepairError, match="INVALID_BCN_ADDRESS"):
        service.request_repair(bot_pk=7, actor_id="ops")
    repair.request_bot.assert_not_called()
