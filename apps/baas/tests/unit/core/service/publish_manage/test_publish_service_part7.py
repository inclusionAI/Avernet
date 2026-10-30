"""Tests for DefaultPublishService."""

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_manage import BotConfig, BotStatus
from secbaas.community.api.device_manage import DeployConfig
from secbaas.community.api.publish_manage import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    BatchStatus,
    DrainResult,
    ProgressSummary,
    PublishConfig,
    PublishConflictError,
    PublishEventType,
    PublishNotFoundError,
    PublishProgressResponse,
    PublishStatus,
    PublishType,
    StageProgress,
)
from secbaas.community.core.repository.publish_batch import (
    PublishBatchRecord,
)
from secbaas.community.core.service.publish_manage import DefaultPublishService


@pytest.fixture
def mock_publish_repo():
    mock = MagicMock()
    mock.now.return_value = datetime.now()
    return mock


@pytest.fixture
def mock_batch_repo():
    return MagicMock()


@pytest.fixture
def mock_record_repo():
    mock = MagicMock()
    mock.count_records_by_batch_id.return_value = {}
    return mock


@pytest.fixture
def mock_device_service():
    mock = MagicMock()
    mock.start_device = AsyncMock()
    mock.restart_device = AsyncMock()
    mock.destroy_device_by_uuid = AsyncMock()
    mock.create_device = MagicMock()
    return mock


@pytest.fixture
def mock_bot_service():
    return MagicMock()


@pytest.fixture
def mock_template_service():
    return MagicMock()


# Module-level service instance — set by autouse fixture so test methods
# can reference it without needing a fixture parameter.
_publish_service_instance = None
_clone_pending_bot = None


@pytest.fixture(autouse=True)
def _stub_clone_pending_bot():
    global _clone_pending_bot
    with patch(
        "secbaas.community.core.service.publish_manage._publish_create.try_insert_pending_bot"
    ) as clone:
        _clone_pending_bot = clone
        yield clone


@pytest.fixture(autouse=True)
def _setup_publish_service(
    mock_publish_repo,
    mock_batch_repo,
    mock_record_repo,
    mock_device_service,
    mock_bot_service,
    mock_template_service,
):
    global _publish_service_instance

    _publish_service_instance = DefaultPublishService(
        bot_repo=MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=mock_publish_repo,
        batch_repo=mock_batch_repo,
        publish_record_repo=mock_record_repo,
        template_service=mock_template_service,
        bot_service=mock_bot_service,
        device_service=mock_device_service,
    )
    yield _publish_service_instance


class TestExecuteUpdateBatchFailedDevice:
    """Tests for _execute_update_batch with FAILED devices.

    Verifies that FAILED devices are included in UPDATE batch and processed
    correctly (skip UPDATING status and drain, go straight to restart).
    """

    @pytest.mark.asyncio
    async def test_update_batch_includes_failed_devices(self):
        """UPDATE batch should include both ACTIVE and FAILED devices."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 3

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"
        mock_bot.extra_config = {}

        mock_target_bot = MagicMock()
        mock_target_bot.id = 2
        mock_target_bot.template_uuid = "template-uuid-1"
        mock_target_bot.extra_config = {
            "deploy_config": {"after_create_cmd_hook": "/bin/echo test"}
        }

        # Two ACTIVE devices + one FAILED device
        mock_active_device = MagicMock()
        mock_active_device.id = 10
        mock_active_device.device_uuid = "device-uuid-active"
        mock_active_device.domain = "test_domain"
        mock_active_device.status = DeviceStatus.ACTIVE.value

        mock_active_device2 = MagicMock()
        mock_active_device2.id = 11
        mock_active_device2.device_uuid = "device-uuid-active-2"
        mock_active_device2.domain = "test_domain"
        mock_active_device2.status = DeviceStatus.ACTIVE.value

        mock_failed_device = MagicMock()
        mock_failed_device.id = 20
        mock_failed_device.device_uuid = "device-uuid-failed"
        mock_failed_device.domain = "test_domain"
        mock_failed_device.status = DeviceStatus.FAILED.value

        mock_env = MagicMock()
        devices = [mock_active_device, mock_active_device2, mock_failed_device]
        device_map = {d.id: d for d in devices}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = device_map

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_records = []
            for i, d in enumerate(devices):
                pr = MagicMock()
                pr.id = 100 + i
                pr.device_id = d.id
                pr.device_uuid = d.device_uuid
                pending_records.append(pr)
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = pending_records

            _publish_service_instance._device_service.update_device = AsyncMock(
                return_value=MagicMock(status=DeviceStatus.ACTIVE.value),
            )
            _publish_service_instance._drain_device = AsyncMock(
                return_value=MagicMock(success=True),
            )
            result = await _publish_service_instance._execute_update_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            # All 3 devices (2 ACTIVE + 1 FAILED) should be processed
            assert result.success is True
            assert result.processed_count == 3
            assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_update_batch_failed_device_set_to_updating_and_drained(self):
        """FAILED device in UPDATE batch should be set to UPDATING and drained like ACTIVE."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"
        mock_bot.extra_config = {}

        mock_target_bot = MagicMock()
        mock_target_bot.id = 2
        mock_target_bot.template_uuid = "template-uuid-1"
        mock_target_bot.extra_config = {
            "deploy_config": {"after_create_cmd_hook": "/bin/echo test"}
        }

        mock_failed_device = MagicMock()
        mock_failed_device.id = 20
        mock_failed_device.device_uuid = "device-uuid-failed"
        mock_failed_device.domain = "test_domain"
        mock_failed_device.status = DeviceStatus.FAILED.value

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                20: mock_failed_device
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_record = MagicMock()
            pending_record.id = 200
            pending_record.device_id = 20
            pending_record.device_uuid = mock_failed_device.device_uuid
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.update_device = AsyncMock(
                return_value=MagicMock(status=DeviceStatus.ACTIVE.value),
            )
            _publish_service_instance._drain_device = AsyncMock()
            result = await _publish_service_instance._execute_update_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            # Verify all devices processed successfully
            assert result.success is True
            assert result.processed_count == 1
            assert result.failed_count == 0

            # FAILED device should be set to UPDATING
            _publish_service_instance._device_repo.update_status_by_device_uuid.assert_called_once_with(
                device_uuid="device-uuid-failed",
                tenant="test_tenant",
                env=mock_env,
                status=DeviceStatus.UPDATING.value,
            )

            # FAILED device should also be drained (no-op, but unified code path)
            _publish_service_instance._drain_device.assert_called_once()

            # restart_device should still be called
            _publish_service_instance._device_service.update_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_batch_active_device_still_goes_through_drain(self):
        """ACTIVE device in UPDATE batch should still go through UPDATING + drain."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"
        mock_bot.extra_config = {}

        mock_target_bot = MagicMock()
        mock_target_bot.id = 2
        mock_target_bot.template_uuid = "template-uuid-1"
        mock_target_bot.extra_config = {
            "deploy_config": {"after_create_cmd_hook": "/bin/echo test"}
        }

        mock_active_device = MagicMock()
        mock_active_device.id = 10
        mock_active_device.device_uuid = "device-uuid-active"
        mock_active_device.domain = "test_domain"
        mock_active_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                10: mock_active_device
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_record = MagicMock()
            pending_record.id = 200
            pending_record.device_id = 10
            pending_record.device_uuid = mock_active_device.device_uuid
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.update_device = AsyncMock(
                return_value=MagicMock(status=DeviceStatus.ACTIVE.value),
            )
            _publish_service_instance._drain_device = AsyncMock(
                return_value=MagicMock(success=True),
            )
            result = await _publish_service_instance._execute_update_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            # Verify success
            assert result.success is True
            assert result.processed_count == 1
            assert result.failed_count == 0

            # ACTIVE device SHOULD have been set to UPDATING
            _publish_service_instance._device_repo.update_status_by_device_uuid.assert_called_once_with(
                device_uuid="device-uuid-active",
                tenant="test_tenant",
                env=mock_env,
                status=DeviceStatus.UPDATING.value,
            )

            # ACTIVE device SHOULD have been drained
            _publish_service_instance._drain_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_batch_only_failed_devices_no_warning(self):
        """UPDATE batch with only FAILED devices should not log 'No ACTIVE devices'."""

        from secbaas.community.api.device_manage import DeviceStatus

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"
        mock_bot.extra_config = {}

        mock_target_bot = MagicMock()
        mock_target_bot.id = 2
        mock_target_bot.template_uuid = "template-uuid-1"
        mock_target_bot.extra_config = {
            "deploy_config": {"after_create_cmd_hook": "/bin/echo test"}
        }

        mock_failed_device = MagicMock()
        mock_failed_device.id = 20
        mock_failed_device.device_uuid = "device-uuid-failed"
        mock_failed_device.domain = "test_domain"
        mock_failed_device.status = DeviceStatus.FAILED.value

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                20: mock_failed_device
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_record = MagicMock()
            pending_record.id = 200
            pending_record.device_id = 20
            pending_record.device_uuid = mock_failed_device.device_uuid
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.update_device = AsyncMock(
                return_value=MagicMock(status=DeviceStatus.ACTIVE.value),
            )
            _publish_service_instance._drain_device = AsyncMock()
            result = await _publish_service_instance._execute_update_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            # FAILED-only batch should succeed
            assert result.success is True
            assert result.processed_count == 1
            assert result.failed_count == 0


class TestPublishRecordStatusMismatchFix:
    """Tests that publish record status reflects device operation outcome,
    even when DeviceService.start_device returns FAILED without raising."""

    @pytest.mark.asyncio
    async def test_create_batch_start_device_returns_failed_status(self):
        """CREATE batch: start_device returns FAILED DeviceResponse → record should be FAILED."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-1"
        mock_device.status = DeviceStatus.PENDING.value

        mock_failed_response = MagicMock()
        mock_failed_response.status = DeviceStatus.FAILED.value
        mock_failed_response.err_msg = "PaaS creation failed: timeout"

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                10: mock_device
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_record = MagicMock()
            pending_record.id = 200
            pending_record.device_id = 10
            pending_record.device_uuid = mock_device.device_uuid
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.start_device = AsyncMock(
                return_value=mock_failed_response
            )
            result = await _publish_service_instance._execute_create_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            assert result.success is False
            assert result.processed_count == 0
            assert result.failed_count == 1

            assert (
                _publish_service_instance._publish_record_repo.update_result.call_count
                == 2
            )
            last_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                -1
            ]
            assert last_call.kwargs["record_id"] == 200
            assert last_call.kwargs["result_status"] == PublishRecordResult.FAILED.value
            assert "PaaS creation failed: timeout" in last_call.kwargs["result_message"]

    @pytest.mark.asyncio
    async def test_update_batch_start_device_returns_failed_status(self):
        """UPDATE batch: restart_device returns FAILED DeviceResponse → record should be FAILED."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"
        mock_bot.extra_config = {}

        mock_target_bot = MagicMock()
        mock_target_bot.id = 2
        mock_target_bot.template_uuid = "template-uuid-1"
        mock_target_bot.extra_config = {
            "deploy_config": {"after_create_cmd_hook": "/bin/echo test"}
        }

        mock_device_record = MagicMock()
        mock_device_record.id = 10
        mock_device_record.device_uuid = "device-uuid-1"
        mock_device_record.domain = "test_domain"
        mock_device_record.status = DeviceStatus.ACTIVE.value

        mock_failed_response = MagicMock()
        mock_failed_response.status = DeviceStatus.FAILED.value
        mock_failed_response.err_msg = "Hook execution failed"

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                10: mock_device_record
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_record = MagicMock()
            pending_record.id = 200
            pending_record.device_id = 10
            pending_record.device_uuid = mock_device_record.device_uuid
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.update_device = AsyncMock(
                return_value=mock_failed_response,
            )
            _publish_service_instance._drain_device = AsyncMock(
                return_value=MagicMock(success=True),
            )
            result = await _publish_service_instance._execute_update_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            assert result.success is False
            assert result.failed_count == 1

            assert (
                _publish_service_instance._publish_record_repo.update_result.call_count
                == 2
            )
            last_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                -1
            ]
            assert last_call.kwargs["result_status"] == PublishRecordResult.FAILED.value
            assert "Hook execution failed" in last_call.kwargs["result_message"]

    @pytest.mark.asyncio
    async def test_restart_batch_restart_device_returns_failed_status(self):
        """RESTART batch: restart_device returns FAILED DeviceResponse → record should be FAILED."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {}

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-1"
        mock_device.domain = "test_domain"
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"

        mock_failed_response = MagicMock()
        mock_failed_response.status = DeviceStatus.FAILED.value
        mock_failed_response.err_msg = "Restart hook failed"

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                10: mock_device
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_record = MagicMock()
            pending_record.id = 200
            pending_record.device_id = 10
            pending_record.device_uuid = mock_device.device_uuid
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.restart_device = AsyncMock(
                return_value=mock_failed_response,
            )
            _publish_service_instance._drain_device = AsyncMock(
                return_value=MagicMock(success=True),
            )
            result = await _publish_service_instance._execute_restart_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=None,
            )

            assert result.success is False
            assert result.failed_count == 1

            assert (
                _publish_service_instance._publish_record_repo.update_result.call_count
                == 2
            )
            last_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                -1
            ]
            assert last_call.kwargs["result_status"] == PublishRecordResult.FAILED.value

    @pytest.mark.asyncio
    async def test_update_batch_restart_device_returns_false(self):
        """UPDATE batch: restart_device returns FAILED → record should be FAILED."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"
        mock_bot.extra_config = {}

        mock_target_bot = MagicMock()
        mock_target_bot.id = 2
        mock_target_bot.template_uuid = "template-uuid-1"
        mock_target_bot.extra_config = {
            "deploy_config": {"after_create_cmd_hook": "/bin/echo test"}
        }

        mock_device_record = MagicMock()
        mock_device_record.id = 10
        mock_device_record.device_uuid = "device-uuid-1"
        mock_device_record.domain = "test_domain"
        mock_device_record.status = DeviceStatus.ACTIVE.value

        mock_failed_response = MagicMock()
        mock_failed_response.status = DeviceStatus.FAILED.value
        mock_failed_response.err_msg = "restart returned False"

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                10: mock_device_record
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_record = MagicMock()
            pending_record.id = 200
            pending_record.device_id = 10
            pending_record.device_uuid = mock_device_record.device_uuid
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.update_device = AsyncMock(
                return_value=mock_failed_response,
            )
            _publish_service_instance._drain_device = AsyncMock(
                return_value=MagicMock(success=True),
            )
            result = await _publish_service_instance._execute_update_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            assert result.success is False
            assert result.failed_count == 1

            assert (
                _publish_service_instance._publish_record_repo.update_result.call_count
                == 2
            )
            last_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                -1
            ]
            assert last_call.kwargs["result_status"] == PublishRecordResult.FAILED.value
            assert "restart returned False" in last_call.kwargs["result_message"]


class TestStateMachineUtils:
    """State machine utility method tests."""

    def test_can_transition_valid_transitions(self):
        """_can_transition returns True for valid state transitions."""
        from secbaas.community.core.service.publish_manage._publish_service import (
            DefaultPublishService,
        )

        assert _publish_service_instance._can_transition("PENDING", "approve") is True
        assert (
            _publish_service_instance._can_transition("ACTIVE", "stage_complete")
            is True
        )
        assert _publish_service_instance._can_transition("APPROVING", "approve") is True
        assert _publish_service_instance._can_transition("ACTIVE", "fail") is True

    def test_can_transition_invalid_transitions(self):
        """_can_transition returns False for invalid state transitions."""
        from secbaas.community.core.service.publish_manage._publish_service import (
            DefaultPublishService,
        )

        assert _publish_service_instance._can_transition("SUCCESS", "approve") is False
        assert _publish_service_instance._can_transition("PENDING", "revoke") is False
        assert _publish_service_instance._can_transition("REJECTED", "approve") is False

    def test_get_next_status_known_transitions(self):
        """_get_next_status returns correct next status for valid transitions."""
        from secbaas.community.core.service.publish_manage._publish_service import (
            DefaultPublishService,
        )

        assert (
            _publish_service_instance._get_next_status("PENDING", "approve") == "ACTIVE"
        )
        assert _publish_service_instance._get_next_status("ACTIVE", "fail") == "FAILED"
        assert (
            _publish_service_instance._get_next_status("APPROVING", "revoke")
            == "REVOKED"
        )

    def test_get_next_status_invalid_returns_none(self):
        """_get_next_status returns None for unknown transitions."""
        from secbaas.community.core.service.publish_manage._publish_service import (
            DefaultPublishService,
        )

        assert _publish_service_instance._get_next_status("SUCCESS", "approve") is None
        assert _publish_service_instance._get_next_status("PENDING", "revoke") is None


class TestUtilityFunctions:
    """Tests for utility/helper functions in publish service."""

    def test_extra_config_to_publish_config(self):
        """_extra_config_to_publish_config converts dict to PublishConfig."""
        from secbaas.community.core.service.publish_manage._publish_service import (
            _extra_config_to_publish_config,
        )

        config = _extra_config_to_publish_config({"target_count": 5})
        assert config is not None
        assert config.target_count == 5

    def test_extra_config_to_publish_config_none(self):
        """_extra_config_to_publish_config returns None for None input."""
        from secbaas.community.core.service.publish_manage._publish_service import (
            _extra_config_to_publish_config,
        )

        assert _extra_config_to_publish_config(None) is None
