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


class TestCheckAndHandleTimeout:
    """Tests for _check_and_handle_timeout method."""

    @pytest.mark.asyncio
    async def test_check_timeout_no_config_uses_default(self):
        """_check_and_handle_timeout uses default timeout when no config."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.extra_config = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_stale_processing_records.return_value = []

            await _publish_service_instance._check_and_handle_timeout(
                publish_record=mock_publish,
                tenant="test_tenant",
            )

            _publish_service_instance._publish_record_repo.list_stale_processing_records.assert_called_once_with(
                publish_id=1,
                timeout_seconds=DEFAULT_CALLBACK_TIMEOUT_SECONDS,
                tenant="test_tenant",
                env=mock_env,
            )

    @pytest.mark.asyncio
    async def test_check_timeout_custom_timeout_from_config(self):
        """_check_and_handle_timeout uses timeout from publish config."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.extra_config = {"callback_timeout_seconds": 300}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_stale_processing_records.return_value = []

            await _publish_service_instance._check_and_handle_timeout(
                publish_record=mock_publish,
                tenant="test_tenant",
            )

            _publish_service_instance._publish_record_repo.list_stale_processing_records.assert_called_once_with(
                publish_id=1,
                timeout_seconds=300,
                tenant="test_tenant",
                env=mock_env,
            )

    @pytest.mark.asyncio
    async def test_check_timeout_no_stale_records_returns_early(self):
        """_check_and_handle_timeout returns early when no stale records."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.extra_config = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_stale_processing_records.return_value = []

            with patch.object(
                DefaultPublishService,
                "handle_device_callback",
                new_callable=AsyncMock,
            ) as mock_callback:
                await _publish_service_instance._check_and_handle_timeout(
                    publish_record=mock_publish,
                    tenant="test_tenant",
                )

                mock_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_timeout_fires_synthetic_failed_callback(self):
        """_check_and_handle_timeout fires FAILED callback for stale records."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.extra_config = None

        mock_stale_record = MagicMock()
        mock_stale_record.id = 42
        mock_stale_record.device_uuid = "device-uuid-stale"
        mock_stale_record.gmt_create = datetime.now()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_stale_processing_records.return_value = [
                mock_stale_record
            ]

            with patch.object(
                DefaultPublishService,
                "handle_device_callback",
                new_callable=AsyncMock,
            ) as mock_callback:
                await _publish_service_instance._check_and_handle_timeout(
                    publish_record=mock_publish,
                    tenant="test_tenant",
                )

                assert mock_callback.call_count == 1
                call_args = mock_callback.call_args
                callback_arg = call_args[0][0]
                assert callback_arg.device_uuid == "device-uuid-stale"
                assert callback_arg.result_status == "FAILED"
                assert callback_arg.exit_code == -1

    @pytest.mark.asyncio
    async def test_check_timeout_skips_record_without_device_uuid(self):
        """_check_and_handle_timeout skips records with no device_uuid."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.extra_config = None

        mock_stale_record = MagicMock()
        mock_stale_record.id = 42
        mock_stale_record.device_uuid = None
        mock_stale_record.gmt_create = datetime.now()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_stale_processing_records.return_value = [
                mock_stale_record
            ]

            with patch.object(
                DefaultPublishService,
                "handle_device_callback",
                new_callable=AsyncMock,
            ) as mock_callback:
                await _publish_service_instance._check_and_handle_timeout(
                    publish_record=mock_publish,
                    tenant="test_tenant",
                )

                mock_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_timeout_callback_exception_handled(self):
        """_check_and_handle_timeout handles exception gracefully."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.extra_config = None

        mock_stale_record = MagicMock()
        mock_stale_record.id = 42
        mock_stale_record.device_uuid = "device-uuid-stale"
        mock_stale_record.gmt_create = datetime.now()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_stale_processing_records.return_value = [
                mock_stale_record
            ]

            with patch.object(
                DefaultPublishService,
                "handle_device_callback",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Callback failed"),
            ):
                await _publish_service_instance._check_and_handle_timeout(
                    publish_record=mock_publish,
                    tenant="test_tenant",
                )


class TestCheckStageAdvancementUpdateFailure:
    """Tests for _check_stage_advancement UPDATE failure cleanup."""

    @pytest.mark.asyncio
    async def test_check_stage_advancement_update_failure_cleans_clone(self):
        """_check_stage_advancement cleans PENDING clone on UPDATE failure."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.UPDATE.value
        mock_publish.extra_config = {}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            mock_batch_repo.return_value = MagicMock()

            with patch.object(
                DefaultPublishService,
                "_cleanup_pending_clone_on_update_failure",
            ) as mock_cleanup:
                await _publish_service_instance._check_stage_advancement(
                    tenant="test_tenant",
                    publish_id=1,
                    current_stage="PROD_FIRST_BATCH",
                    stage_failed=True,
                )

                mock_cleanup.assert_called_once_with(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="callback",
                )


class TestGetPublishProgressWithDevices:
    """Tests for get_publish_progress with include_devices=True."""

    @pytest.mark.asyncio
    async def test_get_publish_progress_include_devices(self):
        """get_publish_progress returns device details when include_devices=True."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = None

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "COMPLETED"
        mock_batch.batch_capacity = 10

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {}
            _publish_service_instance._publish_record_repo.list_by_batch_id.return_value = []

            with patch.object(
                DefaultPublishService,
                "_get_device_details",
                return_value=([], []),
            ):
                result = await _publish_service_instance.get_publish_progress(
                    tenant="test_tenant",
                    publish_id=1,
                    include_devices=True,
                )

                assert result.device_details is not None
                assert result.failed_devices is not None
                assert result.overall_progress.total_batches == 1


class TestGetPendingBatches:
    """Tests for _get_pending_batches static method."""

    def test_get_pending_batches_returns_first_non_completed_stage(self):
        """_get_pending_batches returns stage with non-COMPLETED batches."""
        mock_env = MagicMock()

        mock_batch1 = MagicMock()
        mock_batch1.stage = "PREPUB"
        mock_batch1.status = "COMPLETED"

        mock_batch2 = MagicMock()
        mock_batch2.stage = "PROD_FIRST_BATCH"
        mock_batch2.status = "PENDING"

        mock_batch3 = MagicMock()
        mock_batch3.stage = "PROD_FIRST_BATCH"
        mock_batch3.status = "PENDING"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch1,
                mock_batch2,
                mock_batch3,
            ]

            stage, batches = _publish_service_instance._get_pending_batches(
                tenant="test_tenant", publish_id=1
            )

            assert stage == "PROD_FIRST_BATCH"
            assert len(batches) == 2

    def test_get_pending_batches_all_complete_returns_success(self):
        """_get_pending_batches returns SUCCESS stage when all batches complete."""
        mock_env = MagicMock()

        mock_batch = MagicMock()
        mock_batch.stage = "PROD_OTHER_BATCH"
        mock_batch.status = "COMPLETED"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            stage, batches = _publish_service_instance._get_pending_batches(
                tenant="test_tenant", publish_id=1
            )

            assert stage == "SUCCESS"
            assert batches == []

    def test_get_pending_batches_no_batches_returns_none(self):
        """_get_pending_batches returns None when no batches exist."""
        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = []

            stage, batches = _publish_service_instance._get_pending_batches(
                tenant="test_tenant", publish_id=1
            )

            assert stage is None
            assert batches == []


class TestCleanupPendingClone:
    """Tests for _cleanup_pending_clone_on_update_failure."""

    def test_cleanup_pending_clone_soft_deletes_orphan_bot(self):
        """_cleanup_pending_clone soft-deletes orphan PENDING clone bot."""

        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

        mock_target_bot = MagicMock()
        mock_target_bot.status = BotStatus.PENDING.value

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
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            mock_rel_repo_instance = MagicMock()
            _publish_service_instance._rel_repo = mock_rel_repo_instance

            _publish_service_instance._cleanup_pending_clone_on_update_failure(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
            )

            mock_rel_repo_instance.soft_delete_by_bot_id.assert_called_once_with(
                bot_id=2,
                tenant="test_tenant",
                env=mock_env,
                modifier="admin",
            )
            _publish_service_instance._bot_repo.soft_delete.assert_called_once_with(
                bot_id=2,
                tenant="test_tenant",
                env=mock_env,
                modifier="admin",
            )

    def test_cleanup_pending_clone_publish_not_found_returns_early(self):
        """_cleanup_pending_clone returns early when publish not found."""
        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = None

            _publish_service_instance._cleanup_pending_clone_on_update_failure(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
            )

    def test_cleanup_pending_clone_no_target_bot_id_returns_early(self):
        """_cleanup_pending_clone returns early when no target_bot_id."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _publish_service_instance._cleanup_pending_clone_on_update_failure(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
            )

    def test_cleanup_pending_clone_target_bot_not_found_returns_early(self):
        """_cleanup_pending_clone returns early when target bot not found."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

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
            _publish_service_instance._bot_repo.get_by_id.return_value = None

            _publish_service_instance._cleanup_pending_clone_on_update_failure(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
            )

    def test_cleanup_pending_clone_target_not_pending_returns_early(self):
        """_cleanup_pending_clone returns early when target bot is not PENDING."""

        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"target_bot_id": 2}

        mock_target_bot = MagicMock()
        mock_target_bot.status = BotStatus.ACTIVE.value

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
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            _publish_service_instance._cleanup_pending_clone_on_update_failure(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
            )


class TestCreatePublishScaleValidation:
    """Tests for scale amount derivation in create_publish."""

    @pytest.mark.asyncio
    async def test_create_publish_scale_up_derives_amount(self):
        """SCALE_UP derives scale_amount from replica_desired and active count."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.template_uuid = "template-uuid"
        mock_bot.domain = "test.domain"
        mock_bot.extra_config = {}
        mock_bot.config = None

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "SCALE_UP"
        mock_publish.status = "PENDING"
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_active_device = MagicMock()
        mock_active_device.status = DeviceStatus.ACTIVE.value

        config = PublishConfig(replica_desired=5)
        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                mock_active_device
            ]

            _publish_service_instance._template_service = MagicMock()
            _publish_service_instance._template_service.get_online_template_by_uuid = (
                MagicMock(return_value=MagicMock())
            )
            mock_new_device = MagicMock()
            mock_new_device.device_uuid = "new-device-uuid"
            _publish_service_instance._device_service = MagicMock()
            _publish_service_instance._device_service.create_device = MagicMock(
                return_value=mock_new_device
            )
            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._rel_repo = MagicMock()
            _publish_service_instance._bot_repo = MagicMock()

            result = await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.SCALE_UP,
                operator="user1",
                config=config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result.publish_type == "SCALE_UP"
            assert result.status == "PENDING"

    @pytest.mark.asyncio
    async def test_create_publish_scale_up_invalid_direction(self):
        """SCALE_UP raises when target <= current count."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_active_device1 = MagicMock()
        mock_active_device1.status = DeviceStatus.ACTIVE.value
        mock_active_device2 = MagicMock()
        mock_active_device2.status = DeviceStatus.ACTIVE.value

        config = PublishConfig(replica_desired=1)
        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                mock_active_device1,
                mock_active_device2,
            ]

            with pytest.raises(ValueError, match="SCALE_UP requires target"):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.SCALE_UP,
                    operator="user1",
                    config=config,
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_create_publish_scale_down_raises_invalid_direction(self):
        """SCALE_DOWN raises when target >= current count."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_active_device1 = MagicMock()
        mock_active_device1.status = DeviceStatus.ACTIVE.value
        mock_active_device2 = MagicMock()
        mock_active_device2.status = DeviceStatus.ACTIVE.value

        config = PublishConfig(replica_desired=5)
        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                mock_active_device1,
                mock_active_device2,
            ]

            with pytest.raises(ValueError, match="SCALE_DOWN requires target"):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.SCALE_DOWN,
                    operator="user1",
                    config=config,
                    request_id="test-request-id-12345678901234567890",
                )


class TestCreatePublishOrphanCleanup:
    """Tests for orphan publish auto-cleanup in create_publish."""

    @pytest.mark.asyncio
    async def test_create_publish_cleans_orphan_publish(self):
        """create_publish auto-cleans orphan publish with no batch records."""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_orphan = MagicMock()
        mock_orphan.id = 99
        mock_orphan.publish_type = "CREATE"
        mock_orphan.status = "PENDING"

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.status = "PENDING"
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.list_active_by_bot_ids.return_value = [
            mock_orphan
        ]
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        # Mock get_by_id to return records with batch_index so sorted() works
        mock_batch_record = MagicMock()
        mock_batch_record.batch_index = 0
        mock_batch_record.id = 1
        mock_batch_record.batch_capacity = 5
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.side_effect = [
            [],
            [],
        ]
        _publish_service_instance._publish_batch_repo.insert_batch.return_value = 1
        _publish_service_instance._publish_batch_repo.get_by_id.return_value = (
            mock_batch_record
        )

        # Mock devices with PENDING status so _create_device_records_for_publish
        # has eligible devices (CREATE selects PENDING devices)
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = "PENDING"
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]
        _publish_service_instance._publish_record_repo = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            result = await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.CREATE,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

            assert result.status == "PENDING"
            _publish_service_instance._publish_repo.update_status.assert_called_once_with(
                publish_id=99,
                tenant="test_tenant",
                env=mock_env,
                status=PublishStatus.FAILED.value,
                modifier="user1",
            )
