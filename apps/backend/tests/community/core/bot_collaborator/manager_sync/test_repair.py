"""Repair submits current keys through the ordinary notifier."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.repair import (
    DisabledManagerSyncRepair,
    ManagerSyncRepairService,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncReason,
)


def test_request_bot_delegates_verbatim_without_reading():
    reader = Mock()
    notifier = Mock()
    notifier.request_sync.return_value = SubmissionResult.COALESCED
    repair = ManagerSyncRepairService(reader, notifier)
    key = SyncKey("tenant", "dev", 7)

    assert repair.request_bot(key) is SubmissionResult.COALESCED
    notifier.request_sync.assert_called_once_with(
        bot_pk=7,
        env="dev",
        avernet_tenant="tenant",
        reason=SyncReason.REPAIR,
    )
    reader.assert_not_called()


def test_request_page_clamps_to_100_and_preserves_rejections():
    keys = tuple(SyncKey("tenant", "dev", pk) for pk in range(11, 15))
    reader = Mock(page_keys=Mock(return_value=keys))
    notifier = Mock()
    notifier.request_sync.side_effect = [
        SubmissionResult.QUEUED,
        SubmissionResult.COALESCED,
        SubmissionResult.REJECTED,
        SubmissionResult.DISABLED,
    ]
    repair = ManagerSyncRepairService(reader, notifier)

    page = repair.request_page(
        avernet_tenant="tenant",
        env="dev",
        after_pk=10,
        limit=500,
    )

    reader.page_keys.assert_called_once_with(
        avernet_tenant="tenant",
        env="dev",
        after_pk=10,
        limit=100,
    )
    assert page.last_pk == 14
    assert (page.queued, page.coalesced, page.rejected) == (1, 1, 2)
    assert page.rejected_keys == keys[2:]
    assert notifier.request_sync.call_args_list == [
        call(
            bot_pk=key.bot_pk,
            env="dev",
            avernet_tenant="tenant",
            reason=SyncReason.REPAIR,
        )
        for key in keys
    ]


def test_empty_and_zero_limit_pages_keep_cursor():
    reader = Mock(page_keys=Mock(return_value=()))
    repair = ManagerSyncRepairService(reader, Mock())
    empty = repair.request_page(
        avernet_tenant="tenant", env="dev", after_pk=9, limit=10
    )
    assert empty.last_pk == 9
    assert empty.rejected_keys == ()

    zero = repair.request_page(avernet_tenant="tenant", env="dev", after_pk=9, limit=0)
    assert zero.last_pk == 9
    assert reader.page_keys.call_count == 1


def test_disabled_repair_is_minimal_and_explicit():
    repair = DisabledManagerSyncRepair("disabled")
    assert repair.request_bot(SyncKey("tenant", "dev", 1)) is SubmissionResult.DISABLED
    with pytest.raises(RuntimeError, match="disabled"):
        repair.request_page(avernet_tenant="tenant", env="dev", after_pk=0, limit=100)


def test_repair_has_no_coordination_clear_surface():
    repair = ManagerSyncRepairService(Mock(), Mock())
    assert not hasattr(repair, "clear_confirmed_attempt")
