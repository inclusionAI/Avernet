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
    from secbaas.community.core.service.publish_manage import DefaultPublishService

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


class TestExecuteRestartBatch:
    """Tests for _execute_restart_batch method."""

    @pytest.mark.asyncio
    async def test_execute_restart_batch_calls_device_restart(self):
        """RESTART batch should call DeviceService.restart_device() after drain."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {}

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-1"
        mock_device.domain = "test_domain"
        mock_device.status = DeviceStatus.ACTIVE.value

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
                return_value=MagicMock(
                    status=DeviceStatus.ACTIVE.value,
                )
            )
            _publish_service_instance._drain_device = AsyncMock(
                return_value=DrainResult(
                    success=True,
                    sessions_remaining=0,
                    duration_seconds=0.1,
                    timeout_reached=False,
                )
            )
            result = await _publish_service_instance._execute_restart_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
            )

            # Verify DeviceService.restart_device was called
            _publish_service_instance._device_service.restart_device.assert_called_once_with(
                tenant="test_tenant",
                device_uuid=mock_device.device_uuid,
                modifier="admin",
                publish_id=1,
            )

            # Verify success
            assert result.success is True
            assert result.processed_count == 1
            assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_execute_restart_batch_failure_rolls_back_to_failed(self):
        """RESTART batch failure should roll back device status to FAILED."""
        from secbaas.community.api.device_manage import DeviceStatus

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
            _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None

            with patch.object(
                DefaultPublishService, "_drain_device", new_callable=AsyncMock
            ):
                _publish_service_instance._publish_record_repo = MagicMock()
                pending_record = MagicMock()
                pending_record.id = 100
                pending_record.device_id = 10
                pending_record.device_uuid = "device-uuid-1"
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    pending_record
                ]

                _publish_service_instance._device_service.restart_device = AsyncMock(
                    side_effect=ValueError("Restart failed")
                )
                result = await _publish_service_instance._execute_restart_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )

                # Verify failure is recorded
                assert result.success is False
                assert result.processed_count == 0
                assert result.failed_count == 1

                # Device status set to UPDATING before restart attempt
                # then rolled back to FAILED on exception
                assert (
                    _publish_service_instance._device_repo.update_status_by_device_uuid.call_count
                    == 2
                )
                calls = _publish_service_instance._device_repo.update_status_by_device_uuid.call_args_list
                # First call: set UPDATING
                assert calls[0].kwargs["status"] == DeviceStatus.UPDATING.value
                # Second call: rollback to FAILED
                assert calls[1].kwargs["status"] == DeviceStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_restart_batch_includes_updating_device_when_no_active_publish(self):
        """RESTART batch: UPDATING device included when no other active publish exists."""
        from secbaas.community.api.device_manage import DeviceStatus

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
        mock_device.status = DeviceStatus.UPDATING.value

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
            _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None

            with patch.object(
                DefaultPublishService, "_drain_device", new_callable=AsyncMock
            ):
                _publish_service_instance._publish_record_repo = MagicMock()
                pending_record = MagicMock()
                pending_record.id = 100
                pending_record.device_id = 10
                pending_record.device_uuid = "device-uuid-1"
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    pending_record
                ]

                _publish_service_instance._device_service.restart_device = AsyncMock(
                    return_value=MagicMock(
                        status=DeviceStatus.ACTIVE.value,
                    )
                )
                result = await _publish_service_instance._execute_restart_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )

                assert result.success is True
                assert result.processed_count == 1
                _publish_service_instance._device_service.restart_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_batch_excludes_updating_device_when_active_publish(self):
        """RESTART batch: UPDATING device excluded when another active publish exists."""
        from secbaas.community.api.device_manage import DeviceStatus

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
        mock_device.status = DeviceStatus.UPDATING.value

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {}

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )
            other_active = MagicMock()
            other_active.id = 99
            _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = other_active

            with patch.object(
                DefaultPublishService, "_drain_device", new_callable=AsyncMock
            ):
                _publish_service_instance._publish_record_repo = MagicMock()
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []

                _publish_service_instance._device_service.restart_device = AsyncMock(
                    return_value=MagicMock(
                        status=DeviceStatus.ACTIVE.value,
                    )
                )
                result = await _publish_service_instance._execute_restart_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )

                assert result.success is False
                assert result.processed_count == 0
                assert result.failed_count == 0
                assert "No pending records" in result.error_message

    @pytest.mark.asyncio
    async def test_restart_batch_no_devices_returns_failure(self):
        """RESTART batch with no eligible devices returns failure BatchResult."""

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {}

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {}

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )
            _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []

            result = await _publish_service_instance._execute_restart_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
            )

            assert result.success is False
            assert result.processed_count == 0
            assert "No pending records" in result.error_message


class TestPublishRecordResultLifecycle:
    """Tests for two-phase result_status lifecycle: CREATED → SUCCESS/FAILED."""

    @pytest.mark.asyncio
    async def test_create_batch_success_transitions_to_success(self):
        """CREATE batch: PENDING→CREATED→SUCCESS lifecycle."""
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
            pending_record.id = 42
            pending_record.device_id = 10
            pending_record.device_uuid = "device-uuid-1"
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            async_mock_start = AsyncMock()
            async_mock_start.status = DeviceStatus.ACTIVE.value
            _publish_service_instance._device_service.start_device = AsyncMock(
                return_value=async_mock_start
            )
            result = await _publish_service_instance._execute_create_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            assert result.success is True
            assert result.processed_count == 1
            assert result.failed_count == 0

            # update_result called twice: CREATED then SUCCESS
            assert (
                _publish_service_instance._publish_record_repo.update_result.call_count
                == 2
            )
            cre_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                0
            ]
            assert cre_call.kwargs["record_id"] == 42
            assert (
                cre_call.kwargs["result_status"] == PublishRecordResult.PROCESSING.value
            )
            suc_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                1
            ]
            assert suc_call.kwargs["record_id"] == 42
            assert suc_call.kwargs["result_status"] == PublishRecordResult.SUCCESS.value
            assert suc_call.kwargs["result_message"] == "Device started successfully"

    @pytest.mark.asyncio
    async def test_create_batch_failure_transitions_to_failed(self):
        """CREATE batch failure: PENDING→CREATED→FAILED lifecycle."""
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
            pending_record.id = 42
            pending_record.device_id = 10
            pending_record.device_uuid = "device-uuid-1"
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.start_device = AsyncMock(
                side_effect=RuntimeError("Device start failed")
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
            assert result.failed_count == 1

            # update_result called twice: CREATED then FAILED
            assert (
                _publish_service_instance._publish_record_repo.update_result.call_count
                == 2
            )
            cre_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                0
            ]
            assert cre_call.kwargs["record_id"] == 42
            assert (
                cre_call.kwargs["result_status"] == PublishRecordResult.PROCESSING.value
            )
            fail_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                1
            ]
            assert fail_call.kwargs["record_id"] == 42
            assert fail_call.kwargs["result_status"] == PublishRecordResult.FAILED.value
            assert "Device start failed" in fail_call.kwargs["result_message"]

    @pytest.mark.asyncio
    async def test_restart_batch_failure_creates_failed_record(self):
        """RESTART batch failure: PENDING→CREATED→FAILED lifecycle."""
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

            with patch.object(
                DefaultPublishService, "_drain_device", new_callable=AsyncMock
            ):
                _publish_service_instance._publish_record_repo = MagicMock()
                pending_record = MagicMock()
                pending_record.id = 42
                pending_record.device_id = 10
                pending_record.device_uuid = "device-uuid-1"
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    pending_record
                ]

                _publish_service_instance._device_service.restart_device = AsyncMock(
                    side_effect=ValueError("Restart failed")
                )
                result = await _publish_service_instance._execute_restart_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )

                assert result.success is False
                assert result.failed_count == 1

                # update_result called twice: CREATED then FAILED
                assert (
                    _publish_service_instance._publish_record_repo.update_result.call_count
                    == 2
                )
                cre_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                    0
                ]
                assert cre_call.kwargs["record_id"] == 42
                assert (
                    cre_call.kwargs["result_status"]
                    == PublishRecordResult.PROCESSING.value
                )
                fail_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                    1
                ]
                assert fail_call.kwargs["record_id"] == 42
                assert (
                    fail_call.kwargs["result_status"]
                    == PublishRecordResult.FAILED.value
                )
                assert "Restart failed" in fail_call.kwargs["result_message"]

    @pytest.mark.asyncio
    async def test_scale_up_batch_creates_and_updates_record(self):
        """SCALE_UP batch: PENDING→CREATED→SUCCESS lifecycle."""
        from secbaas.community.api.publish_manage import (
            PublishRecordResult,
        )

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"
        mock_bot.extra_config = {}

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

            _publish_service_instance._publish_record_repo = MagicMock()
            pending_record = MagicMock()
            pending_record.id = 42
            pending_record.device_id = 999
            pending_record.device_uuid = "device-uuid-1"
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_repo = MagicMock()
            mock_device = MagicMock()
            mock_device.id = 999
            mock_device.device_uuid = "device-uuid-1"
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                999: mock_device
            }

            _publish_service_instance._template_service.get_online_template_by_uuid = (
                MagicMock(
                    return_value=MagicMock(),
                )
            )
            _publish_service_instance._device_service.start_device = AsyncMock(
                return_value=MagicMock(status="ACTIVE")
            )

            result = await _publish_service_instance._execute_scale_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                publish_type=PublishType.SCALE_UP.value,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            assert result.success is True
            assert result.processed_count == 1

            # update_result called twice: CREATED then SUCCESS
            assert (
                _publish_service_instance._publish_record_repo.update_result.call_count
                == 2
            )
            cre_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                0
            ]
            assert (
                cre_call.kwargs["result_status"] == PublishRecordResult.PROCESSING.value
            )
            suc_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                1
            ]
            assert suc_call.kwargs["result_status"] == PublishRecordResult.SUCCESS.value
            assert suc_call.kwargs["result_message"] == "Device scaled up successfully"

    @pytest.mark.asyncio
    async def test_result_message_truncation(self):
        """Exception messages longer than 4000 chars are truncated in result_message."""
        from secbaas.community.api.device_manage import DeviceStatus

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

        mock_env = MagicMock()
        long_error = "x" * 5000

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
            pending_record.id = 42
            pending_record.device_id = 10
            pending_record.device_uuid = "device-uuid-1"
            _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                pending_record
            ]

            _publish_service_instance._device_service.start_device = AsyncMock(
                side_effect=RuntimeError(long_error)
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
            # update_result called twice: CREATED then FAILED
            fail_call = _publish_service_instance._publish_record_repo.update_result.call_args_list[
                1
            ]
            assert len(fail_call.kwargs["result_message"]) == 4000
