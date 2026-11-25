"""Shared best-effort post-commit manager-sync notifications."""

from __future__ import annotations

import logging
from unittest.mock import Mock

from agentclaw.community.core.bot_collaborator.manager_sync.notifications import (
    notify_bot_manager_sync,
    notify_space_manager_sync,
)
from agentclaw.community.kernel.bot_manager_sync import SpaceChange, SyncReason


def test_bot_notification_forwards_exact_hint() -> None:
    sync = Mock()

    notify_bot_manager_sync(
        sync,
        bot_pk=17,
        env="pre",
        avernet_tenant="tenant-a",
        reason=SyncReason.OWNER_HANDOFF,
    )

    sync.request_sync.assert_called_once_with(
        bot_pk=17,
        env="pre",
        avernet_tenant="tenant-a",
        reason=SyncReason.OWNER_HANDOFF,
    )


def test_bot_notification_failure_is_swallowed_and_logs_bot_id(caplog) -> None:
    sync = Mock()
    sync.request_sync.side_effect = RuntimeError("secret downstream detail")

    with caplog.at_level(logging.ERROR, logger="start"):
        notify_bot_manager_sync(
            sync,
            bot_pk=23,
            env="dev",
            avernet_tenant="tenant-a",
            reason=SyncReason.BOT_SPACE_CHANGED,
        )

    record = caplog.records[-1]
    assert record.getMessage() == "bot manager sync notification failed"
    assert record.bot_pk == 23
    assert record.env == "dev"
    assert record.reason == SyncReason.BOT_SPACE_CHANGED.value
    assert record.exception_type == "RuntimeError"
    assert "secret downstream detail" not in caplog.text


def test_space_notification_forwards_exact_change() -> None:
    sync = Mock()
    change = SpaceChange("tenant-a", "dev", ("7", "team:7"), ("member-1",))

    notify_space_manager_sync(sync, change=change, space_id=7)

    sync.request_space_sync.assert_called_once_with(change)


def test_space_notification_failure_is_swallowed_and_logs_safe_counts(caplog) -> None:
    sync = Mock()
    sync.request_space_sync.side_effect = RuntimeError("member-1 secret")
    change = SpaceChange("tenant-a", "dev", ("7", "team:7"), ("member-1",))

    with caplog.at_level(logging.ERROR, logger="start"):
        notify_space_manager_sync(sync, change=change, space_id=7)

    record = caplog.records[-1]
    assert record.getMessage() == "space manager sync notification failed"
    assert record.space_id == 7
    assert record.avernet_tenant == "tenant-a"
    assert record.env == "dev"
    assert record.space_ref_count == 2
    assert record.user_id_count == 1
    assert record.exception_type == "RuntimeError"
    assert "member-1 secret" not in caplog.text
