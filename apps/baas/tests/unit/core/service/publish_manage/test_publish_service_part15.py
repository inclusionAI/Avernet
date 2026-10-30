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


class TestExecuteScaleBatchNotFound:
    """Tests for _execute_scale_batch not-found path (line 2512)."""

    @pytest.mark.asyncio
    async def test_execute_scale_batch_publish_not_found(self):
        """_execute_scale_batch raises PublishNotFoundError."""
        mock_batch = MagicMock()
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = None

            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance._execute_scale_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    publish_type=PublishType.SCALE_UP.value,
                    operator="admin",
                )


class TestExecuteScaleBatchBotNotFound:
    """Tests for _execute_scale_batch bot not-found path (lines 2520-2521)."""

    @pytest.mark.asyncio
    async def test_execute_scale_batch_bot_not_found(self):
        """_execute_scale_batch returns failure when bot not found."""
        mock_batch = MagicMock()
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 999

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = None

            result = await _publish_service_instance._execute_scale_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                publish_type=PublishType.SCALE_UP.value,
                operator="admin",
                publish_record=mock_publish,
                bot_record=None,
            )

            assert result.success is False
            assert "Bot not found" in str(result.error_message)


class TestExecuteScaleBatchNoTemplate:
    """Tests for _execute_scale_batch no template_uuid/template not found (lines 2531-2544)."""

    @pytest.mark.asyncio
    async def test_execute_scale_batch_no_template_uuid(self):
        """SCALE_UP returns failure when bot has no template_uuid."""
        mock_batch = MagicMock()
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.template_uuid = None

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            result = await _publish_service_instance._execute_scale_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                publish_type=PublishType.SCALE_UP.value,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            assert result.success is False
            assert "no template_uuid" in str(result.error_message)


class TestExecuteDestroyBatchNotFound:
    """Tests for _execute_destroy_batch not-found path (line 2833)."""

    @pytest.mark.asyncio
    async def test_execute_destroy_batch_publish_not_found(self):
        """_execute_destroy_batch raises PublishNotFoundError."""
        mock_batch = MagicMock()
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = None

            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance._execute_destroy_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )


class TestDrainDeviceMockMode:
    """Tests for _drain_device mock mode fast path (lines 3024-3025)."""

    @pytest.mark.asyncio
    async def test_drain_device_mock_mode_skips_drain(self):
        """_drain_device returns immediately in mock mode."""
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.is_paas_mock_mode",
            return_value=True,
        ):
            result = await _publish_service_instance._drain_device(
                tenant="test_tenant",
                device_id=1,
                timeout_seconds=30,
            )

            assert result.success is True
            assert result.duration_seconds == 0
            assert result.timeout_reached is False


class TestHandleDeviceCallbackNoBatchId:
    """Tests for handle_device_callback no batch_id path (lines 3252-3256)."""

    @pytest.mark.asyncio
    async def test_device_callback_no_batch_id_returns_warning(self):
        """Callback on record with no batch_id returns warning."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            DeviceCallbackRequest,
            PublishRecordResult,
        )

        mock_env = MagicMock()
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "dev-1"
        mock_device.status = DeviceStatus.PENDING.value

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.result_status = PublishRecordResult.PROCESSING.value
        mock_publish_record.batch_id = None

        callback = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            tenant="test_tenant",
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_device_uuid.return_value = (
                mock_device
            )

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.get_processing_record_by_device_and_publish.return_value = mock_publish_record
            _publish_service_instance._publish_record_repo.update_record_cas.return_value = True

            result = await _publish_service_instance.handle_device_callback(callback)

            assert result["status"] == "processed"
            assert "warning" in result


class TestCheckStageAdvancementPublishNotFound:
    """Tests for _check_stage_advancement publish not-found (line 3334)."""

    @pytest.mark.asyncio
    async def test_check_stage_advancement_publish_not_found(self):
        """_check_stage_advancement returns early when publish not found."""
        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = None

            # Should not raise
            await _publish_service_instance._check_stage_advancement(
                tenant="test_tenant",
                publish_id=1,
                current_stage="PREPUB",
                stage_failed=False,
            )


class TestCheckStageAdvancementDestroyFailed:
    """Tests for _check_stage_advancement DESTROY failure cleanup (lines 3362-3393)."""

    @pytest.mark.asyncio
    async def test_check_stage_advancement_destroy_failed_cleanup(self):
        """DESTROY stage failure triggers device cleanup."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.DESTROY.value

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.DESTROYING.value

        mock_device = MagicMock()
        mock_device.device_uuid = "dev-1"
        mock_device.status = DeviceStatus.UPDATING.value

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

            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                mock_device
            ]

            await _publish_service_instance._check_stage_advancement(
                tenant="test_tenant",
                publish_id=1,
                current_stage="PROD_FIRST_BATCH",
                stage_failed=True,
            )

            _publish_service_instance._publish_repo.update_status.assert_called_with(
                publish_id=1,
                tenant="test_tenant",
                env=mock_env,
                status=PublishStatus.FAILED.value,
                modifier="callback",
            )
            _publish_service_instance._bot_repo.complete_destroy.assert_called_once()
            _publish_service_instance._device_repo.update_status_by_device_uuid.assert_called()


class TestCompletePublishNotFound:
    """Tests for complete_publish not-found path (line 3529)."""

    @pytest.mark.asyncio
    async def test_complete_publish_not_found_raises(self):
        """complete_publish raises PublishNotFoundError when publish missing."""
        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(None, None),
        ):
            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance.complete_publish(
                    tenant="test_tenant",
                    publish_id=999,
                    operator="admin",
                )


class TestCompletePublishLazyFetch:
    """Tests for complete_publish lazy bot fetch (lines 3554-3555)."""

    @pytest.mark.asyncio
    async def test_complete_publish_lazy_fetches_bot_record(self):
        """complete_publish fetches bot_record when None is passed."""

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.publish_type = PublishType.CREATE.value
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.ACTIVE.value

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

            # bot_record=None → lazy fetch → update_status called
            _publish_service_instance._bot_repo.update_status.return_value = None

            result = await _publish_service_instance.complete_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                publish_record=mock_publish,
                bot_record=None,
            )

            assert result.status == PublishStatus.SUCCESS.value
            _publish_service_instance._bot_repo.get_by_id.assert_called_once()


class TestCompletePublishDestroyCleanupException:
    """Tests for complete_publish DESTROY cleanup exception (lines 3574-3575)."""

    @pytest.mark.asyncio
    async def test_complete_publish_destroy_cleanup_exception(self):
        """complete_publish handles DESTROY cleanup exception gracefully."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.publish_type = PublishType.DESTROY.value
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.complete_destroy.side_effect = (
                RuntimeError("DB error")
            )

            result = await _publish_service_instance.complete_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            # Should still succeed — exception is caught and logged
            assert result.status == PublishStatus.SUCCESS.value


class TestCompletePublishUpdateTransferException:
    """Tests for complete_publish UPDATE transfer exception (lines 3634-3638)."""

    @pytest.mark.asyncio
    async def test_complete_publish_update_transfer_raises(self):
        """complete_publish re-raises UPDATE transfer failure."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.publish_type = PublishType.UPDATE.value
        mock_publish.extra_config = {"target_bot_id": 2}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._bot_repo = MagicMock()

            mock_rel_repo_instance = MagicMock()
            mock_rel_repo_instance.list_by_bot_id.return_value = [
                MagicMock(device_uuid="dev-1", domain="test_domain")
            ]
            _publish_service_instance._rel_repo = mock_rel_repo_instance

            # Make complete_update_transfer raise
            _publish_service_instance._bot_repo.complete_update_transfer.side_effect = (
                RuntimeError("Transfer failed")
            )

            with pytest.raises(RuntimeError, match="Transfer failed"):
                await _publish_service_instance.complete_publish(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="admin",
                    publish_record=mock_publish,
                    bot_record=mock_bot,
                )


class TestCompletePublishActiveStatusFailure:
    """Tests for complete_publish bot ACTIVE status update failure (lines 3665-3666)."""

    @pytest.mark.asyncio
    async def test_complete_publish_active_status_update_fails(self):
        """complete_publish handles bot status update failure gracefully."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.publish_type = PublishType.CREATE.value
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.update_status.side_effect = (
                RuntimeError("DB error")
            )

            result = await _publish_service_instance.complete_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            # Should still succeed
            assert result.status == PublishStatus.SUCCESS.value


class TestAggregateStageProgressPartial:
    """Tests for _aggregate_stage_progress partially complete stage (line 3875)."""

    def test_aggregate_stage_progress_partial_complete(self):
        """_aggregate_stage_progress sets ACTIVE for partially complete stage."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1

        mock_batch1 = MagicMock()
        mock_batch1.id = 1
        mock_batch1.batch_index = 0
        mock_batch1.batch_capacity = 5
        mock_batch1.status = BatchStatus.COMPLETED.value
        mock_batch1.stage = "PROD_FIRST_BATCH"

        mock_batch2 = MagicMock()
        mock_batch2.id = 2
        mock_batch2.batch_index = 1
        mock_batch2.batch_capacity = 5
        mock_batch2.status = BatchStatus.PENDING.value
        mock_batch2.stage = "PROD_FIRST_BATCH"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {
                "SUCCESS": 5
            }

            stages = _publish_service_instance._aggregate_stage_progress(
                batches=[mock_batch1, mock_batch2],
                publish_id=1,
                tenant="test_tenant",
                record_repo=_publish_service_instance._publish_record_repo,
            )

            assert len(stages) == 1
            assert stages[0].stage == "PROD_FIRST_BATCH"
            # 1 of 2 batches completed → ACTIVE
            assert stages[0].status == PublishStatus.ACTIVE.value


class TestComputeOverallProgressZeroDevices:
    """Tests for _compute_overall_progress zero devices (line 3924)."""

    def test_compute_overall_progress_zero_devices(self):
        """_compute_overall_progress handles zero total_devices."""
        mock_batch = MagicMock()
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 0  # zero devices
        mock_batch.status = BatchStatus.COMPLETED.value

        result = _publish_service_instance._compute_overall_progress(
            batches=[mock_batch],
            status_counts={"COMPLETED": 0},
        )

        assert result.total_devices == 0
        assert result.progress_percentage == 0.0


class TestExecuteUpdateBatchLazyFetch:
    """Tests for _execute_update_batch lazy fetch paths (lines 2032-2035, 2038-2041, 2060-2061)."""

    @pytest.mark.asyncio
    async def test_execute_update_batch_lazy_fetch_publish_not_found(self):
        """_execute_update_batch raises when lazy fetch returns None."""
        mock_batch = MagicMock()
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = None

            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance._execute_update_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                    publish_record=None,
                    bot_record=None,
                )

    @pytest.mark.asyncio
    async def test_execute_update_batch_bot_not_found(self):
        """_execute_update_batch returns failure when bot not found."""
        mock_batch = MagicMock()
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 999
        mock_publish.extra_config = {}

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = None

            result = await _publish_service_instance._execute_update_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=None,
            )

            assert result.success is False
            assert "Bot not found" in str(result.error_message)

    @pytest.mark.asyncio
    async def test_execute_update_batch_no_target_bot_id(self):
        """_execute_update_batch uses current bot when no target_bot_id."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {}  # no target_bot_id

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.extra_config = {}
        mock_bot.template_uuid = "tpl-1"

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "dev-1"
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                mock_device
            ]

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.get_by_device_id_and_publish_id.return_value = None

            with patch.object(
                DefaultPublishService,
                "_drain_device",
                new_callable=AsyncMock,
            ) as mock_drain:
                mock_drain.return_value = MagicMock(success=True)

                # Mock DefaultDeviceService.restart_device to simulate failure
                # so we exit the loop quickly without PaaS calls
                _publish_service_instance._device_service.restart_device = AsyncMock(
                    side_effect=RuntimeError("Restart error")
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

                assert result.success is True  # no devices to process = success
                assert result.processed_count == 0


class TestGetPublishBotUuid:
    """Tests for get_publish_bot_uuid(tenant, publish_id) -> str."""

    @pytest.fixture(autouse=True)
    def _setup_bot_repo(self):
        """Ensure bot_repo is a fresh MagicMock for each test."""
        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._publish_repo = MagicMock()

    @pytest.mark.asyncio
    async def test_returns_bot_uuid_for_valid_publish(self):
        """get_publish_bot_uuid returns bot_uuid string for valid publish_id with matching tenant."""
        mock_bot_uuid = "test-bot-uuid-123"
        mock_bot_id = 42
        mock_publish = MagicMock()
        mock_publish.bot_id = mock_bot_id
        mock_bot = MagicMock()
        mock_bot.bot_uuid = mock_bot_uuid

        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish
        _publish_service_instance._bot_repo.get_by_id_including_deleted.return_value = (
            mock_bot
        )

        result = await _publish_service_instance.get_publish_bot_uuid(
            tenant="test_tenant", publish_id=1
        )

        assert result == mock_bot_uuid
        _publish_service_instance._publish_repo.get_by_id.assert_called_once_with(
            1, tenant="test_tenant", env=ANY
        )
        _publish_service_instance._bot_repo.get_by_id_including_deleted.assert_called_once_with(
            mock_bot_id, tenant="test_tenant", env=ANY
        )

    @pytest.mark.asyncio
    async def test_raises_not_found_when_publish_does_not_exist(self):
        """get_publish_bot_uuid raises PublishNotFoundError when publish_id not found."""
        _publish_service_instance._publish_repo.get_by_id.return_value = None

        with pytest.raises(PublishNotFoundError):
            await _publish_service_instance.get_publish_bot_uuid(
                tenant="test_tenant", publish_id=999
            )

    @pytest.mark.asyncio
    async def test_raises_not_found_when_bot_not_found(self):
        """get_publish_bot_uuid raises PublishNotFoundError when publish exists but bot record missing."""
        mock_publish = MagicMock()
        mock_publish.bot_id = 42

        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish
        _publish_service_instance._bot_repo.get_by_id_including_deleted.return_value = (
            None
        )

        with pytest.raises(PublishNotFoundError):
            await _publish_service_instance.get_publish_bot_uuid(
                tenant="test_tenant", publish_id=1
            )

    @pytest.mark.asyncio
    async def test_returns_bot_uuid_for_destroy_publish_with_soft_deleted_bot(self):
        """get_publish_bot_uuid uses get_by_id_including_deleted to handle DESTROY publish case."""
        mock_bot_uuid = "destroyed-bot-uuid"
        mock_bot_id = 99
        mock_publish = MagicMock()
        mock_publish.bot_id = mock_bot_id
        mock_bot = MagicMock()
        mock_bot.bot_uuid = mock_bot_uuid

        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish
        _publish_service_instance._bot_repo.get_by_id_including_deleted.return_value = (
            mock_bot
        )

        result = await _publish_service_instance.get_publish_bot_uuid(
            tenant="test_tenant", publish_id=1
        )

        assert result == mock_bot_uuid
        # Verify get_by_id_including_deleted is used (not get_by_id)
        _publish_service_instance._bot_repo.get_by_id_including_deleted.assert_called_once()
        _publish_service_instance._bot_repo.get_by_id.assert_not_called()
