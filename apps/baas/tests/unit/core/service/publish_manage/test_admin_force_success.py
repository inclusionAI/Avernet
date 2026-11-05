"""Tests for DefaultPublishAdminService force_success.

Covers:
- force_success happy path (records -> batches -> devices -> bot -> publish)
- PublishNotFoundError when publish_id doesn't exist
- _update_batches_and_records: partial updates (some already SUCCESS/COMPLETED)
- _update_devices_and_bot: no bot_id, all already ACTIVE
- Edge cases: empty batches, repo exceptions

Mock builders shared with the other admin-service tests live in
``_admin_test_fixtures``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.publish_manage import PublishNotFoundError
from secbaas.community.core.service.publish_manage._admin_service import (
    DefaultPublishAdminService,
    ForceSuccessResult,
)
from tests.unit.core.service.publish_manage._admin_test_fixtures import (
    _make_mock_batch,
    _make_mock_bot,
    _make_mock_device,
    _make_mock_record,
    _make_service,
)


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

    async def test_force_success_update_publish_retires_clone_before_success(self):
        """force_success: UPDATE publish retires its clone BEFORE the SUCCESS write."""
        mock_publish = MagicMock()
        mock_publish.status = "PENDING"
        mock_publish.bot_id = 42
        mock_publish.publish_type = "UPDATE"
        mock_publish.extra_config = {"target_bot_id": 77}

        pub_repo = MagicMock()
        pub_repo.get_by_id.return_value = mock_publish
        batch_repo = MagicMock()
        batch_repo.list_by_publish_id.return_value = []
        device_repo = MagicMock()
        device_repo.list_by_bot_id.return_value = []

        service = _make_service(
            publish_repo=pub_repo,
            batch_repo=batch_repo,
            device_repo=device_repo,
        )

        with (
            patch(
                "secbaas.community.core.service.publish_manage._admin_service.get_current_env",
                return_value="test",
            ),
            patch.object(
                DefaultPublishAdminService, "_retire_pending_clone"
            ) as mock_retire,
        ):
            ordering = MagicMock()
            ordering.attach_mock(mock_retire, "retire_clone")
            ordering.attach_mock(pub_repo.update_status, "update_status")

            await service.force_success(
                publish_id=1,
                tenant="test-tenant",
                modifier="test-user",
            )

        mock_retire.assert_called_once_with(
            tenant="test-tenant",
            env="test",
            target_bot_id=77,
            modifier="test-user",
        )
        pub_repo.update_status.assert_called_once_with(
            publish_id=1,
            tenant="test-tenant",
            env="test",
            status="SUCCESS",
            modifier="test-user",
        )
        call_names = [name for name, _, _ in ordering.mock_calls]
        assert call_names.index("retire_clone") < call_names.index("update_status")

    async def test_force_success_update_publish_without_target_bot_id_skips_retire(
        self,
    ):
        """force_success: UPDATE publish lacking an int target_bot_id skips retirement.

        The clone retire is guarded by isinstance(target_bot_id, int): a
        publish without extra_config (or without the pointer) must still
        reach the terminal SUCCESS write.
        """
        mock_publish = MagicMock()
        mock_publish.status = "PENDING"
        mock_publish.bot_id = 42
        mock_publish.publish_type = "UPDATE"
        mock_publish.extra_config = None

        pub_repo = MagicMock()
        pub_repo.get_by_id.return_value = mock_publish
        batch_repo = MagicMock()
        batch_repo.list_by_publish_id.return_value = []
        device_repo = MagicMock()
        device_repo.list_by_bot_id.return_value = []

        service = _make_service(
            publish_repo=pub_repo,
            batch_repo=batch_repo,
            device_repo=device_repo,
        )

        with (
            patch(
                "secbaas.community.core.service.publish_manage._admin_service.get_current_env",
                return_value="test",
            ),
            patch.object(
                DefaultPublishAdminService, "_retire_pending_clone"
            ) as mock_retire,
        ):
            await service.force_success(
                publish_id=1,
                tenant="test-tenant",
                modifier="test-user",
            )

        mock_retire.assert_not_called()
        pub_repo.update_status.assert_called_once_with(
            publish_id=1,
            tenant="test-tenant",
            env="test",
            status="SUCCESS",
            modifier="test-user",
        )


class TestRetirePendingClone:
    """_retire_pending_clone direct behavior (soft-delete of a PENDING clone)."""

    def _make_clone_bot(
        self,
        *,
        id: int = 77,
        status: str = "PENDING",
    ) -> MagicMock:
        bot = MagicMock()
        bot.id = id
        bot.status = status
        return bot

    def test_pending_row_soft_deletes_rels_then_bot(self):
        """A live PENDING clone gets rel soft-delete then bot soft-delete."""
        clone = self._make_clone_bot(id=77, status="PENDING")
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = clone
        rel_repo = MagicMock()

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        service._retire_pending_clone(
            tenant="test-tenant",
            env="prod",
            target_bot_id=77,
            modifier="test-user",
        )

        bot_repo.get_by_id.assert_called_once_with(77, tenant="test-tenant", env="prod")
        rel_repo.soft_delete_by_bot_id.assert_called_once_with(
            bot_id=77,
            tenant="test-tenant",
            env="prod",
            modifier="test-user",
        )
        bot_repo.soft_delete.assert_called_once_with(
            bot_id=77,
            tenant="test-tenant",
            env="prod",
            modifier="test-user",
        )

    def test_missing_row_is_a_noop(self):
        """No clone row means nothing to retire."""
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = None
        rel_repo = MagicMock()

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        service._retire_pending_clone(
            tenant="test-tenant",
            env="prod",
            target_bot_id=77,
            modifier="test-user",
        )

        rel_repo.soft_delete_by_bot_id.assert_not_called()
        bot_repo.soft_delete.assert_not_called()

    def test_non_pending_row_is_a_noop(self):
        """A clone already promoted (non-PENDING) must not be retired."""
        clone = self._make_clone_bot(id=77, status="ACTIVE")
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = clone
        rel_repo = MagicMock()

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        service._retire_pending_clone(
            tenant="test-tenant",
            env="prod",
            target_bot_id=77,
            modifier="test-user",
        )

        rel_repo.soft_delete_by_bot_id.assert_not_called()
        bot_repo.soft_delete.assert_not_called()

    def test_rel_soft_delete_failure_tolerated_bot_still_deleted(self):
        """A rel soft-delete failure is swallowed; the bot delete still runs."""
        clone = self._make_clone_bot(id=77, status="PENDING")
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = clone
        rel_repo = MagicMock()
        rel_repo.soft_delete_by_bot_id.side_effect = Exception("rel write failed")

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        service._retire_pending_clone(
            tenant="test-tenant",
            env="prod",
            target_bot_id=77,
            modifier="test-user",
        )

        bot_repo.soft_delete.assert_called_once_with(
            bot_id=77,
            tenant="test-tenant",
            env="prod",
            modifier="test-user",
        )

    def test_bot_soft_delete_failure_propagates(self):
        """Per design D2 a bot soft-delete failure must propagate (no terminal write)."""
        clone = self._make_clone_bot(id=77, status="PENDING")
        bot_repo = MagicMock()
        bot_repo.get_by_id.return_value = clone
        bot_repo.soft_delete.side_effect = Exception("bot write failed")
        rel_repo = MagicMock()

        service = _make_service(bot_repo=bot_repo, rel_repo=rel_repo)

        with pytest.raises(Exception, match="bot write failed"):
            service._retire_pending_clone(
                tenant="test-tenant",
                env="prod",
                target_bot_id=77,
                modifier="test-user",
            )


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
