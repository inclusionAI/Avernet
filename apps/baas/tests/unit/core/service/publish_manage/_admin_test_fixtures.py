"""Shared mock builders for DefaultPublishAdminService unit tests."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from secbaas.community.core.repository.bot import (
    BotRecord,
    RestoredBotRecord,
)
from secbaas.community.core.service.publish_manage._admin_service import (
    DefaultPublishAdminService,
)


def _make_mock_batch(
    id: int = 1,
    status: str = "PENDING",
) -> MagicMock:
    batch = MagicMock()
    batch.id = id
    batch.status = status
    return batch


def _make_mock_record(
    id: int = 1,
    result_status: str | None = None,
) -> MagicMock:
    record = MagicMock()
    record.id = id
    record.result_status = result_status
    return record


def _make_mock_device(
    id: int = 1,
    status: str = "INACTIVE",
) -> MagicMock:
    device = MagicMock()
    device.id = id
    device.status = status
    return device


def _make_mock_bot(
    id: int = 1,
    status: str = "ACTIVE",
    tenant: str = "acme",
    env: str = "prod",
    bot_uuid: str = "BOT_U",
) -> MagicMock:
    bot = MagicMock()
    bot.id = id
    bot.status = status
    bot.tenant = tenant
    bot.env = env
    bot.bot_uuid = bot_uuid
    return bot


def _make_service(
    publish_repo: MagicMock | None = None,
    batch_repo: MagicMock | None = None,
    record_repo: MagicMock | None = None,
    device_repo: MagicMock | None = None,
    bot_repo: MagicMock | None = None,
    rel_repo: MagicMock | None = None,
) -> DefaultPublishAdminService:
    return DefaultPublishAdminService(
        publish_repo=publish_repo or MagicMock(),
        batch_repo=batch_repo or MagicMock(),
        record_repo=record_repo or MagicMock(),
        device_repo=device_repo or MagicMock(),
        bot_repo=bot_repo or MagicMock(),
        rel_repo=rel_repo or MagicMock(),
    )


def _make_restored_snapshot(
    *,
    bot_id: int = 7,
    bot_uuid: str = "bot-abc-123",
    new_status: str = "ACTIVE",
    previous_status: str = "PENDING",
    previous_is_deleted: int = 7,
    retired_sibling_count: int = 2,
) -> RestoredBotRecord:
    now = datetime.now()
    record = BotRecord(
        id=bot_id,
        gmt_create=now,
        gmt_modified=now,
        bot_uuid=bot_uuid,
        tenant="acme",
        env="prod",
        domain="example.com",
        is_deleted=0,
        creator="creator-1",
        modifier="ops.alice",
        status=new_status,
        name="Restored Bot",
        description="desc",
        template_uuid="tpl-1",
        replica_desired=1,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        extra_config={},
    )
    return RestoredBotRecord(
        record=record,
        previous_status=previous_status,
        previous_is_deleted=previous_is_deleted,
        retired_sibling_count=retired_sibling_count,
    )
