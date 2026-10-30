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


class TestExecuteScaleBatchScaleDown:
    """Tests for _execute_scale_batch SCALE_DOWN branch."""

    @pytest.mark.asyncio
    async def test_execute_scale_batch_scale_down_destroys_active_devices(self):
        """SCALE_DOWN batch destroys ACTIVE devices and marks them RELEASED."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-scale-down"
        mock_device.domain = "test_domain"
        mock_device.provider_device_id = "provider-1"
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        mock_drain = MagicMock()
        mock_drain.success = True
        mock_drain.sessions_remaining = 0

        mock_destroy_response = MagicMock()
        mock_destroy_response.success = True
        mock_destroy_response.error_message = None
        mock_destroy_response.hook_result = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            pending_record = MagicMock()
            pending_record.id = 42
            pending_record.device_id = mock_device.id
            pending_record.device_uuid = mock_device.device_uuid

            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                mock_device.id: mock_device
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            with patch.object(
                DefaultPublishService,
                "_drain_device",
                new_callable=AsyncMock,
                return_value=mock_drain,
            ):
                _publish_service_instance._publish_record_repo = MagicMock()
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    pending_record
                ]

                _publish_service_instance._device_service.destroy_device_by_uuid = (
                    AsyncMock(
                        new_callable=AsyncMock, return_value=mock_destroy_response
                    )
                )
                mock_rel = MagicMock()
                mock_rel.id = 100
                mock_rel.device_uuid = "device-uuid-scale-down"
                mock_rel_repo_instance = MagicMock()
                mock_rel_repo_instance.list_by_bot_id.return_value = [mock_rel]
                _publish_service_instance._rel_repo = mock_rel_repo_instance

                result = await _publish_service_instance._execute_scale_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    publish_type=PublishType.SCALE_DOWN.value,
                    operator="admin",
                )

                assert result.success is True
                assert result.processed_count == 1
                assert result.failed_count == 0

                # Verify record lifecycle: PENDING → CREATED → SUCCESS
                update_calls = _publish_service_instance._publish_record_repo.update_result.call_args_list
                assert len(update_calls) == 2
                assert (
                    update_calls[0].kwargs["result_status"]
                    == PublishRecordResult.PROCESSING.value
                )
                assert (
                    update_calls[1].kwargs["result_status"]
                    == PublishRecordResult.SUCCESS.value
                )

                # Verify relationship soft-deleted
                mock_rel_repo_instance.soft_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_scale_batch_scale_down_failure(self):
        """SCALE_DOWN batch record insert failure transition."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-scale-down"
        mock_device.domain = "test_domain"
        mock_device.provider_device_id = "provider-1"
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        mock_drain = MagicMock()
        mock_drain.success = True
        mock_drain.sessions_remaining = 0

        mock_destroy_response = MagicMock()
        mock_destroy_response.success = False
        mock_destroy_response.error_message = "Scale down destruction failed"
        mock_destroy_response.hook_result = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            mock_pending_record = MagicMock()
            mock_pending_record.id = 42
            mock_pending_record.device_id = mock_device.id
            mock_pending_record.device_uuid = mock_device.device_uuid

            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                mock_device.id: mock_device
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            with patch.object(
                DefaultPublishService,
                "_drain_device",
                new_callable=AsyncMock,
                return_value=mock_drain,
            ):
                _publish_service_instance._publish_record_repo = MagicMock()
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    mock_pending_record
                ]

                _publish_service_instance._device_service.destroy_device_by_uuid = (
                    AsyncMock(
                        new_callable=AsyncMock, return_value=mock_destroy_response
                    )
                )
                result = await _publish_service_instance._execute_scale_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    publish_type=PublishType.SCALE_DOWN.value,
                    operator="admin",
                )

                assert result.success is False
                assert result.failed_count == 1

                update_calls = _publish_service_instance._publish_record_repo.update_result.call_args_list
                assert (
                    update_calls[-1].kwargs["result_status"]
                    == PublishRecordResult.FAILED.value
                )

    @pytest.mark.asyncio
    async def test_execute_scale_batch_scale_down_exception_handling(self):
        """SCALE_DOWN batch handles exceptions gracefully."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-scale-down"
        mock_device.domain = "test_domain"
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        mock_drain = MagicMock()
        mock_drain.success = True
        mock_drain.sessions_remaining = 0

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            mock_pending_record = MagicMock()
            mock_pending_record.id = 42
            mock_pending_record.device_id = mock_device.id
            mock_pending_record.device_uuid = mock_device.device_uuid

            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                mock_device.id: mock_device
            }

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            with patch.object(
                DefaultPublishService,
                "_drain_device",
                new_callable=AsyncMock,
                return_value=mock_drain,
            ):
                _publish_service_instance._publish_record_repo = MagicMock()
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    mock_pending_record
                ]

                _publish_service_instance._device_service.destroy_device_by_uuid = (
                    AsyncMock(
                        new_callable=AsyncMock,
                        side_effect=RuntimeError("Unexpected scale down error"),
                    )
                )
                result = await _publish_service_instance._execute_scale_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    publish_type=PublishType.SCALE_DOWN.value,
                    operator="admin",
                )

                assert result.success is False
                assert result.failed_count == 1

                update_calls = _publish_service_instance._publish_record_repo.update_result.call_args_list
                assert (
                    update_calls[-1].kwargs["result_status"]
                    == PublishRecordResult.FAILED.value
                )


class TestHandleDeviceCallback:
    """Tests for handle_device_callback method."""

    @pytest.mark.asyncio
    async def test_device_callback_success_updates_device_and_record(self):
        """SUCCESS callback updates device to ACTIVE and record to SUCCESS."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            DeviceCallbackRequest,
            PublishRecordResult,
        )

        callback = DeviceCallbackRequest(
            device_uuid="device-uuid-cb",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            exit_code=0,
            stdout="ok",
            stderr=None,
            tenant="test_tenant",
        )

        mock_env = MagicMock()

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-cb"

        mock_record = MagicMock()
        mock_record.id = 42
        mock_record.batch_id = 1
        mock_record.result_status = PublishRecordResult.PROCESSING.value

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_device_uuid.return_value = (
                mock_device
            )

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.get_processing_record_by_device_and_publish.return_value = mock_record
            _publish_service_instance._publish_record_repo.update_result_if_processing.return_value = True

            with patch.object(
                DefaultPublishService,
                "_check_batch_completion",
                new_callable=AsyncMock,
            ):
                result = await _publish_service_instance.handle_device_callback(
                    callback=callback
                )

                assert result["status"] == "processed"

                _publish_service_instance._device_repo.update_device.assert_called_once_with(
                    device_id=10,
                    tenant="test_tenant",
                    env=mock_env,
                    modifier="callback",
                    status=DeviceStatus.ACTIVE.value,
                )

                _publish_service_instance._publish_record_repo.update_result_if_processing.assert_called_once_with(
                    record_id=42,
                    tenant="test_tenant",
                    env=mock_env,
                    result_status=PublishRecordResult.SUCCESS.value,
                    result_message=ANY,
                    modifier="callback",
                )

    @pytest.mark.asyncio
    async def test_device_callback_failure_updates_device_to_failed(self):
        """FAILED callback updates device to FAILED and record to FAILED."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            DeviceCallbackRequest,
            PublishRecordResult,
        )

        callback = DeviceCallbackRequest(
            device_uuid="device-uuid-cb",
            publish_id=1,
            event_type="start",
            result_status="FAILED",
            exit_code=1,
            stdout=None,
            stderr="Hook failed message",
            tenant="test_tenant",
        )

        mock_env = MagicMock()

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-cb"

        mock_record = MagicMock()
        mock_record.id = 42
        mock_record.batch_id = 1
        mock_record.result_status = PublishRecordResult.PROCESSING.value

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_device_uuid.return_value = (
                mock_device
            )

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.get_processing_record_by_device_and_publish.return_value = mock_record
            _publish_service_instance._publish_record_repo.update_result_if_processing.return_value = True

            with patch.object(
                DefaultPublishService,
                "_check_batch_completion",
                new_callable=AsyncMock,
            ):
                result = await _publish_service_instance.handle_device_callback(
                    callback=callback
                )

                assert result["status"] == "processed"

                _publish_service_instance._device_repo.update_device.assert_called_once_with(
                    device_id=10,
                    tenant="test_tenant",
                    env=mock_env,
                    modifier="callback",
                    status=DeviceStatus.FAILED.value,
                    err_msg="Hook failed message",
                )

                _publish_service_instance._publish_record_repo.update_result_if_processing.assert_called_once_with(
                    record_id=42,
                    tenant="test_tenant",
                    env=mock_env,
                    result_status=PublishRecordResult.FAILED.value,
                    result_message=ANY,
                    modifier="callback",
                )

    @pytest.mark.asyncio
    async def test_device_callback_ignores_non_start_event_type(self):
        """Callback with non-'start' event_type is ignored."""
        from secbaas.community.api.publish_manage import DeviceCallbackRequest

        callback = DeviceCallbackRequest(
            device_uuid="device-uuid-cb",
            publish_id=1,
            event_type="stop",
            result_status="SUCCESS",
            exit_code=0,
            stdout="ok",
            stderr=None,
            tenant="test_tenant",
        )

        result = await _publish_service_instance.handle_device_callback(
            callback=callback
        )

        assert result["status"] == "ignored"
        assert "only start callbacks" in result["reason"]

    @pytest.mark.asyncio
    async def test_device_callback_rejects_invalid_result_status(self):
        """Callback with invalid result_status is rejected."""
        from secbaas.community.api.publish_manage import DeviceCallbackRequest

        callback = DeviceCallbackRequest(
            device_uuid="device-uuid-cb",
            publish_id=1,
            event_type="start",
            result_status="PENDING",
            exit_code=0,
            stdout="ok",
            stderr=None,
            tenant="test_tenant",
        )

        result = await _publish_service_instance.handle_device_callback(
            callback=callback
        )

        assert result["status"] == "rejected"
        assert "invalid result_status" in result["reason"]

    @pytest.mark.asyncio
    async def test_device_callback_device_not_found_raises(self):
        """Callback raises error when device is not found."""
        from secbaas.community.api.publish_manage import DeviceCallbackRequest

        callback = DeviceCallbackRequest(
            device_uuid="device-uuid-cb",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            exit_code=0,
            stdout="ok",
            stderr=None,
            tenant="test_tenant",
        )

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_device_uuid.return_value = (
                None
            )

            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance.handle_device_callback(
                    callback=callback
                )

    @pytest.mark.asyncio
    async def test_device_callback_no_processing_record_ignores(self):
        """Callback ignores when no CREATED record exists (idempotent)."""
        from secbaas.community.api.publish_manage import DeviceCallbackRequest

        callback = DeviceCallbackRequest(
            device_uuid="device-uuid-cb",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            exit_code=0,
            stdout="ok",
            stderr=None,
            tenant="test_tenant",
        )

        mock_env = MagicMock()

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-cb"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_device_uuid.return_value = (
                mock_device
            )

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.get_processing_record_by_device_and_publish.return_value = None

            result = await _publish_service_instance.handle_device_callback(
                callback=callback
            )

            assert result["status"] == "ignored"
            assert "no PROCESSING record" in result["reason"]

    @pytest.mark.asyncio
    async def test_device_callback_concurrent_callback_ignored(self):
        """Callback is ignored when concurrent callback already processed record."""
        from secbaas.community.api.publish_manage import (
            DeviceCallbackRequest,
            PublishRecordResult,
        )

        callback = DeviceCallbackRequest(
            device_uuid="device-uuid-cb",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            exit_code=0,
            stdout="ok",
            stderr=None,
            tenant="test_tenant",
        )

        mock_env = MagicMock()

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-cb"

        mock_record = MagicMock()
        mock_record.id = 42
        mock_record.batch_id = 1
        mock_record.result_status = PublishRecordResult.PROCESSING.value

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_device_uuid.return_value = (
                mock_device
            )

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.get_processing_record_by_device_and_publish.return_value = mock_record
            _publish_service_instance._publish_record_repo.update_result_if_processing.return_value = False

            result = await _publish_service_instance.handle_device_callback(
                callback=callback
            )

            assert result["status"] == "ignored"
            assert "concurrent" in result["reason"]


class TestCheckBatchCompletion:
    """Tests for _check_batch_completion method."""

    @pytest.mark.asyncio
    async def test_check_batch_completion_all_success(self):
        """_check_batch_completion marks batch COMPLETED when all records succeed."""
        mock_env = MagicMock()

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {
                "PROCESSING": 0,
                "FAILED": 0,
                "SUCCESS": 5,
            }

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.get_by_id.return_value = (
                mock_batch
            )

            with patch.object(
                DefaultPublishService,
                "_check_stage_advancement",
                new_callable=AsyncMock,
            ):
                await _publish_service_instance._check_batch_completion(
                    tenant="test_tenant",
                    batch_id=1,
                    publish_id=1,
                )

                _publish_service_instance._publish_batch_repo.update_status.assert_called_once_with(
                    batch_id=1,
                    tenant="test_tenant",
                    env=mock_env,
                    status=BatchStatus.COMPLETED.value,
                    modifier="callback",
                )

    @pytest.mark.asyncio
    async def test_check_batch_completion_with_failures(self):
        """_check_batch_completion marks batch FAILED on any record failure."""
        mock_env = MagicMock()

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {
                "PROCESSING": 0,
                "FAILED": 1,
                "SUCCESS": 4,
            }

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.get_by_id.return_value = (
                mock_batch
            )

            with patch.object(
                DefaultPublishService,
                "_check_stage_advancement",
                new_callable=AsyncMock,
            ) as mock_advance:
                await _publish_service_instance._check_batch_completion(
                    tenant="test_tenant",
                    batch_id=1,
                    publish_id=1,
                )

                _publish_service_instance._publish_batch_repo.update_status.assert_called_once_with(
                    batch_id=1,
                    tenant="test_tenant",
                    env=mock_env,
                    status=BatchStatus.FAILED.value,
                    modifier="callback",
                )

                mock_advance.assert_called_once_with(
                    tenant="test_tenant",
                    publish_id=1,
                    current_stage="PROD_FIRST_BATCH",
                    stage_failed=True,
                )

    @pytest.mark.asyncio
    async def test_check_batch_completion_still_pending_returns_early(self):
        """_check_batch_completion returns early when CREATED records remain."""
        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {
                "PROCESSING": 3,
                "FAILED": 0,
                "SUCCESS": 2,
            }

            _publish_service_instance._publish_batch_repo = MagicMock()

            await _publish_service_instance._check_batch_completion(
                tenant="test_tenant",
                batch_id=1,
                publish_id=1,
            )

            _publish_service_instance._publish_batch_repo.update_status.assert_not_called()
