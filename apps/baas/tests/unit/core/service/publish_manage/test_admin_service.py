"""Tests for admin_service — DefaultPublishAdminService.

Covers:
- force_success happy path (records → batches → devices → bot → publish)
- PublishNotFoundError when publish_id doesn't exist
- _update_batches_and_records: partial updates (some already SUCCESS/COMPLETED)
- _update_devices_and_bot: no bot_id, all already ACTIVE
- Edge cases: empty batches, repo exceptions
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.publish_manage import (
    PublishNotFoundError,
    UpdateBotStatusResult,
)
from secbaas.community.core.service.publish_manage._admin_service import (
    DefaultPublishAdminService,
    ForceSuccessResult,
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
) -> DefaultPublishAdminService:
    return DefaultPublishAdminService(
        publish_repo=publish_repo or MagicMock(),
        batch_repo=batch_repo or MagicMock(),
        record_repo=record_repo or MagicMock(),
        device_repo=device_repo or MagicMock(),
        bot_repo=bot_repo or MagicMock(),
    )


# ============== force_success ==============


class TestForceSuccess:
    """force_success top-level orchestration."""

    async def test_force_success_happy_path(self):
        """Full success path: records -> batches -> devices -> bot -> publish."""
        mock_publish = MagicMock()
        mock_publish.status = "PENDING"
        mock_publish.bot_id = 42

        mock_batch = _make_mock_batch(id=10, status="PENDING")
        mock_record = _make_mock_record(id=100, result_status="PENDING")
        mock_device = _make_mock_device(id=200, status="INACTIVE")

        pub_repo = MagicMock()
        pub_repo.get_by_id.return_value = mock_publish

        batch_repo = MagicMock()
        batch_repo.list_by_publish_id.return_value = [mock_batch]

        record_repo = MagicMock()
        record_repo.list_by_batch_id.return_value = [mock_record]

        device_repo = MagicMock()
        device_repo.list_by_bot_id.return_value = [mock_device]

        bot_repo = MagicMock()

        service = _make_service(
            publish_repo=pub_repo,
            batch_repo=batch_repo,
            record_repo=record_repo,
            device_repo=device_repo,
            bot_repo=bot_repo,
        )

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.get_current_env",
            return_value="test",
        ):
            result = await service.force_success(
                publish_id=1,
                tenant="test-tenant",
                modifier="test-user",
            )

        assert isinstance(result, ForceSuccessResult)
        assert result.publish_id == 1
        assert result.previous_publish_status == "PENDING"
        assert result.records_updated == 1
        assert result.batches_updated == 1
        assert result.devices_updated == 1
        assert result.bot_updated is True

        pub_repo.update_status.assert_called_once_with(
            publish_id=1,
            tenant="test-tenant",
            env="test",
            status="SUCCESS",
            modifier="test-user",
        )

    async def test_force_success_publish_not_found(self):
        """force_success should raise PublishNotFoundError."""
        pub_repo = MagicMock()
        pub_repo.get_by_id.return_value = None
        service = _make_service(publish_repo=pub_repo)

        with (
            patch(
                "secbaas.community.core.service.publish_manage._admin_service.get_current_env",
                return_value="test",
            ),
            pytest.raises(PublishNotFoundError),
        ):
            await service.force_success(
                publish_id=999,
                tenant="test-tenant",
                modifier="test-user",
            )

    async def test_force_success_no_bot_id(self):
        """When publish has no bot_id, devices_updated=0 and bot_updated=False."""
        mock_publish = MagicMock()
        mock_publish.status = "PENDING"
        mock_publish.bot_id = None

        pub_repo = MagicMock()
        pub_repo.get_by_id.return_value = mock_publish

        batch_repo = MagicMock()
        batch_repo.list_by_publish_id.return_value = []

        service = _make_service(
            publish_repo=pub_repo,
            batch_repo=batch_repo,
        )

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.get_current_env",
            return_value="test",
        ):
            result = await service.force_success(
                publish_id=1,
                tenant="test-tenant",
                modifier="test-user",
            )

        assert result.devices_updated == 0
        assert result.bot_updated is False


# ============== _update_batches_and_records ==============


class TestUpdateBatchesAndRecords:
    """_update_batches_and_records internal method."""

    def test_updates_pending_records_and_batches(self):
        """Pending records get SUCCESS, pending batches get COMPLETED."""
        batch = _make_mock_batch(id=1, status="PENDING")
        record = _make_mock_record(id=1, result_status="PENDING")

        batch_repo = MagicMock()
        batch_repo.list_by_publish_id.return_value = [batch]
        record_repo = MagicMock()
        record_repo.list_by_batch_id.return_value = [record]

        service = _make_service(batch_repo=batch_repo, record_repo=record_repo)

        batches_updated, records_updated = service._update_batches_and_records(
            publish_id=1,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert records_updated == 1
        assert batches_updated == 1
        record_repo.update_result.assert_called_once_with(
            record_id=1,
            tenant="test-tenant",
            env="test",
            result_status="SUCCESS",
            result_message=None,
            modifier="test-user",
        )
        batch_repo.update_status.assert_called_once_with(
            batch_id=1,
            tenant="test-tenant",
            env="test",
            status="COMPLETED",
            modifier="test-user",
        )

    def test_skips_already_success_records_and_completed_batches(self):
        """Already successful records/batches should not be updated."""
        batch = _make_mock_batch(id=1, status="COMPLETED")
        record = _make_mock_record(id=1, result_status="SUCCESS")

        batch_repo = MagicMock()
        batch_repo.list_by_publish_id.return_value = [batch]
        record_repo = MagicMock()
        record_repo.list_by_batch_id.return_value = [record]

        service = _make_service(batch_repo=batch_repo, record_repo=record_repo)

        batches_updated, records_updated = service._update_batches_and_records(
            publish_id=1,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert records_updated == 0
        assert batches_updated == 0
        record_repo.update_result.assert_not_called()
        batch_repo.update_status.assert_not_called()

    def test_empty_batches(self):
        """No batches should result in zero updates."""
        batch_repo = MagicMock()
        batch_repo.list_by_publish_id.return_value = []

        service = _make_service(batch_repo=batch_repo)

        batches_updated, records_updated = service._update_batches_and_records(
            publish_id=1,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert records_updated == 0
        assert batches_updated == 0
        service._record_repo.list_by_batch_id.assert_not_called()

    def test_multiple_batches_and_records(self):
        """Multiple batches and records should all be updated."""
        batches = [
            _make_mock_batch(id=1, status="PENDING"),
            _make_mock_batch(id=2, status="PENDING"),
        ]
        records_for_batch_1 = [
            _make_mock_record(id=1, result_status="PENDING"),
            _make_mock_record(id=2, result_status="PENDING"),
        ]
        records_for_batch_2 = [
            _make_mock_record(id=3, result_status="PENDING"),
        ]

        batch_repo = MagicMock()
        batch_repo.list_by_publish_id.return_value = batches
        record_repo = MagicMock()
        record_repo.list_by_batch_id.side_effect = [
            records_for_batch_1,
            records_for_batch_2,
        ]

        service = _make_service(batch_repo=batch_repo, record_repo=record_repo)

        batches_updated, records_updated = service._update_batches_and_records(
            publish_id=1,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert records_updated == 3
        assert batches_updated == 2


# ============== _update_devices_and_bot ==============


class TestUpdateDevicesAndBot:
    """_update_devices_and_bot internal method."""

    def test_updates_inactive_devices_and_bot(self):
        """Inactive devices get ACTIVE, bot gets ACTIVE."""
        device = _make_mock_device(id=1, status="INACTIVE")

        device_repo = MagicMock()
        device_repo.list_by_bot_id.return_value = [device]
        bot_repo = MagicMock()

        service = _make_service(device_repo=device_repo, bot_repo=bot_repo)

        devices_updated, bot_updated = service._update_devices_and_bot(
            bot_id=42,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert devices_updated == 1
        assert bot_updated is True
        device_repo.update_status.assert_called_once_with(
            device_id=1,
            tenant="test-tenant",
            env="test",
            status="ACTIVE",
        )
        bot_repo.update_status.assert_called_once_with(
            bot_id=42,
            tenant="test-tenant",
            env="test",
            status="ACTIVE",
            modifier="test-user",
        )

    def test_skips_already_active_devices(self):
        """Already active devices should not be updated."""
        device = _make_mock_device(id=1, status="ACTIVE")

        device_repo = MagicMock()
        device_repo.list_by_bot_id.return_value = [device]
        bot_repo = MagicMock()

        service = _make_service(device_repo=device_repo, bot_repo=bot_repo)

        devices_updated, bot_updated = service._update_devices_and_bot(
            bot_id=42,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert devices_updated == 0
        assert bot_updated is True
        device_repo.update_status.assert_not_called()

    def test_no_bot_id_returns_zero(self):
        """When bot_id is None, no devices or bot should be updated."""
        service = _make_service()

        devices_updated, bot_updated = service._update_devices_and_bot(
            bot_id=None,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert devices_updated == 0
        assert bot_updated is False

    def test_device_repo_exception_logged_and_continues(self):
        """If device_repo.list_by_bot_id raises, bot update still proceeds."""
        device_repo = MagicMock()
        device_repo.list_by_bot_id.side_effect = Exception("DB error")
        bot_repo = MagicMock()

        service = _make_service(device_repo=device_repo, bot_repo=bot_repo)

        devices_updated, bot_updated = service._update_devices_and_bot(
            bot_id=42,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert devices_updated == 0
        assert bot_updated is True

    def test_bot_repo_exception_logged(self):
        """If bot_repo.update_status raises, bot_updated should be False."""
        device = _make_mock_device(id=1, status="INACTIVE")

        device_repo = MagicMock()
        device_repo.list_by_bot_id.return_value = [device]
        bot_repo = MagicMock()
        bot_repo.update_status.side_effect = Exception("Bot update failed")

        service = _make_service(device_repo=device_repo, bot_repo=bot_repo)

        devices_updated, bot_updated = service._update_devices_and_bot(
            bot_id=42,
            tenant="test-tenant",
            env="test",
            modifier="test-user",
        )

        assert devices_updated == 1
        assert bot_updated is False


# ============== ForceSuccessResult Dataclass ==============


class TestForceSuccessResult:
    """ForceSuccessResult dataclass contract."""

    def test_construction(self):
        result = ForceSuccessResult(
            publish_id=1,
            previous_publish_status="PENDING",
            batches_updated=2,
            records_updated=5,
            devices_updated=3,
            bot_updated=True,
        )
        assert result.publish_id == 1
        assert result.previous_publish_status == "PENDING"
        assert result.batches_updated == 2
        assert result.records_updated == 5
        assert result.devices_updated == 3
        assert result.bot_updated is True


# ============== update_bot_status ==============


class TestUpdateBotStatus:
    """DefaultPublishAdminService.update_bot_status."""

    async def test_update_bot_status_happy_path(self):
        mock_bot = _make_mock_bot(
            id=42,
            status="ACTIVE",
            tenant="acme",
            env="prod",
            bot_uuid="bot-abc-123",
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        result = await service.update_bot_status(
            bot_id=42,
            status="STOPPED",
            operator="ops.alice",
        )

        assert isinstance(result, UpdateBotStatusResult)
        assert result.bot_id == 42
        assert result.bot_uuid == "bot-abc-123"
        assert result.previous_status == "ACTIVE"
        assert result.new_status == "STOPPED"

        bot_repo.get_by_id_only.assert_called_once_with(42)
        bot_repo.update_status.assert_called_once_with(
            bot_id=42,
            tenant="acme",
            env="prod",
            status="STOPPED",
            modifier="ops.alice",
        )

    async def test_update_bot_status_not_found_raises(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = None

        service = _make_service(bot_repo=bot_repo)

        with pytest.raises(PublishNotFoundError) as exc_info:
            await service.update_bot_status(
                bot_id=999,
                status="STOPPED",
                operator="ops.alice",
            )

        assert "999" in str(exc_info.value)
        bot_repo.update_status.assert_not_called()

    async def test_update_bot_status_does_not_call_get_current_env(self):
        mock_bot = _make_mock_bot(id=42, status="ACTIVE", bot_uuid="bot-abc-123")
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.get_current_env",
        ) as mock_get_env:
            await service.update_bot_status(
                bot_id=42,
                status="STOPPED",
                operator="ops.alice",
            )

        mock_get_env.assert_not_called()

    async def test_update_bot_status_emits_warning_log(self):
        mock_bot = _make_mock_bot(
            id=42,
            status="ACTIVE",
            tenant="acme",
            env="prod",
            bot_uuid="bot-abc-123",
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        with patch(
            "secbaas.community.core.service.publish_manage._admin_service.logger"
        ) as mock_logger:
            await service.update_bot_status(
                bot_id=42,
                status="STOPPED",
                operator="ops.alice",
            )

        mock_logger.warning.assert_called_once()
        log_msg = mock_logger.warning.call_args.args[0]
        assert "ADMIN_UPDATE_BOT_STATUS:" in log_msg
        assert "bot_id=42" in log_msg
        assert "bot_uuid=bot-abc-123" in log_msg
        assert "previous_status=ACTIVE" in log_msg
        assert "new_status=STOPPED" in log_msg
        assert "operator=ops.alice" in log_msg
        assert "tenant=acme" in log_msg
        assert "env=prod" in log_msg

    async def test_update_bot_status_captures_different_previous_status(self):
        mock_bot = _make_mock_bot(
            id=42, status="STOPPED", tenant="acme", env="prod", bot_uuid="bot-abc-123"
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        result = await service.update_bot_status(
            bot_id=42,
            status="ACTIVE",
            operator="ops.alice",
        )

        assert result.previous_status == "STOPPED"
        assert result.new_status == "ACTIVE"

        bot_repo.update_status.assert_called_once_with(
            bot_id=42,
            tenant="acme",
            env="prod",
            status="ACTIVE",
            modifier="ops.alice",
        )

    async def test_update_bot_status_uses_record_env_not_server_env(self):
        mock_bot = _make_mock_bot(
            id=42, status="ACTIVE", tenant="acme", env="gray", bot_uuid="bot-abc-123"
        )
        bot_repo = MagicMock()
        bot_repo.get_by_id_only.return_value = mock_bot

        service = _make_service(bot_repo=bot_repo)

        await service.update_bot_status(
            bot_id=42,
            status="STOPPED",
            operator="ops.alice",
        )

        kwargs = bot_repo.update_status.call_args.kwargs
        assert kwargs["env"] == "gray"
        assert kwargs["tenant"] == "acme"
        assert kwargs["bot_id"] == 42


class TestUpdateBotStatusResult:
    """UpdateBotStatusResult dataclass contract."""

    def test_construction(self):
        result = UpdateBotStatusResult(
            bot_id=42,
            bot_uuid="bot-abc-123",
            previous_status="ACTIVE",
            new_status="STOPPED",
        )
        assert result.bot_id == 42
        assert result.bot_uuid == "bot-abc-123"
        assert result.previous_status == "ACTIVE"
        assert result.new_status == "STOPPED"

    def test_construction_with_other_values(self):
        result = UpdateBotStatusResult(
            bot_id=999,
            bot_uuid="abc-def-456",
            previous_status="PENDING",
            new_status="ACTIVE",
        )
        assert result.bot_id == 999
        assert result.bot_uuid == "abc-def-456"
        assert result.previous_status == "PENDING"
        assert result.new_status == "ACTIVE"
