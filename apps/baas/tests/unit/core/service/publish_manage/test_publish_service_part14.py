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


class TestApproveStageAlreadyActive:
    """Tests for approve_stage — already ACTIVE re-drives execution (lines 1064-1071)."""

    @pytest.mark.asyncio
    async def test_approve_stage_already_active_continues_execution(self):
        """approve_stage with ACTIVE status continues auto-execution."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.extra_config = None

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with patch.object(
                DefaultPublishService,
                "_get_publish_and_bot_record",
                return_value=(mock_publish, mock_bot),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_auto_execute_stages",
                    new_callable=AsyncMock,
                ) as mock_exec:
                    with patch.object(
                        DefaultPublishService,
                        "_refresh_publish_response",
                        new_callable=AsyncMock,
                    ) as mock_refresh:
                        mock_refresh.return_value = MagicMock(status="ACTIVE")

                        result = await _publish_service_instance.approve_stage(
                            tenant="test_tenant",
                            publish_id=1,
                            operator="admin",
                        )

                        assert result.status == "ACTIVE"
                        mock_exec.assert_called_once()


class TestApproveStageNotFound:
    """Tests for approve_stage not-found path (line 1053)."""

    @pytest.mark.asyncio
    async def test_approve_stage_publish_not_found_raises(self):
        """approve_stage raises PublishNotFoundError when publish missing."""
        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(None, None),
        ):
            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance.approve_stage(
                    tenant="test_tenant",
                    publish_id=999,
                    operator="admin",
                )


class TestApproveStageInvalidStatus:
    """Tests for approve_stage invalid status (lines 1107, 1084, 1115)."""

    @pytest.mark.asyncio
    async def test_approve_stage_rejected_status_raises(self):
        """approve_stage on REJECTED publish raises ValueError."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.status = PublishStatus.REJECTED.value
        mock_publish.extra_config = None

        mock_bot = MagicMock()
        mock_bot.id = 1

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            with pytest.raises(ValueError, match="Cannot approve"):
                await _publish_service_instance.approve_stage(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="admin",
                )

    @pytest.mark.asyncio
    async def test_approve_stage_failed_status_raises(self):
        """approve_stage on FAILED publish raises ValueError."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.status = PublishStatus.FAILED.value
        mock_publish.extra_config = None

        mock_bot = MagicMock()
        mock_bot.id = 1

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            with pytest.raises(ValueError, match="Cannot approve"):
                await _publish_service_instance.approve_stage(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="admin",
                )


class TestAutoExecuteStagesErrors:
    """Tests for _auto_execute_stages (lines 1165, 1178-1179)."""

    @pytest.mark.asyncio
    async def test_auto_execute_stages_no_batches_returns_early(self):
        """_auto_execute_stages returns early when no pending batches."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=(None, []),
            ):
                # Should not raise
                await _publish_service_instance._auto_execute_stages(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="admin",
                )

    @pytest.mark.asyncio
    async def test_auto_execute_stages_execute_raises_logged(self):
        """_auto_execute_stages handles execute_stage exception gracefully."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_batch = MagicMock()
        mock_batch.id = 1

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PREPUB", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "execute_stage",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("Stage execution failed"),
                ):
                    # Should not raise — exception swallowed
                    await _publish_service_instance._auto_execute_stages(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )


class TestRejectPublishNotFound:
    """Tests for reject_publish not-found path (line 1210)."""

    @pytest.mark.asyncio
    async def test_reject_publish_not_found_raises(self):
        """reject_publish raises PublishNotFoundError when publish missing."""
        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(None, None),
        ):
            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance.reject_publish(
                    tenant="test_tenant",
                    publish_id=999,
                    operator="admin",
                    reason="test",
                )


class TestRejectPublishInvalidStatus:
    """Tests for reject_publish invalid status (line 1214)."""

    @pytest.mark.asyncio
    async def test_reject_publish_active_status_raises(self):
        """reject_publish on ACTIVE publish raises ValueError."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            with pytest.raises(ValueError, match="Cannot reject"):
                await _publish_service_instance.reject_publish(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="admin",
                    reason="test",
                )


class TestRevokePublishNotFound:
    """Tests for revoke_publish not-found path (line 1259)."""

    @pytest.mark.asyncio
    async def test_revoke_publish_not_found_raises(self):
        """revoke_publish raises PublishNotFoundError when publish missing."""
        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(None, None),
        ):
            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance.revoke_publish(
                    tenant="test_tenant",
                    publish_id=999,
                    operator="admin",
                )


class TestRevokePublishInvalidStatus:
    """Tests for revoke_publish invalid status (line 1263)."""

    @pytest.mark.asyncio
    async def test_revoke_publish_active_status_raises(self):
        """revoke_publish on ACTIVE publish raises ValueError."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            with pytest.raises(ValueError, match="Cannot revoke"):
                await _publish_service_instance.revoke_publish(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="admin",
                )


class TestExecuteStageNotFound:
    """Tests for execute_stage not-found path (line 1455)."""

    @pytest.mark.asyncio
    async def test_execute_stage_publish_not_found_raises(self):
        """execute_stage raises PublishNotFoundError when publish missing."""
        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with patch.object(
                DefaultPublishService,
                "_get_publish_and_bot_record",
                return_value=(None, None),
            ):
                with pytest.raises(PublishNotFoundError):
                    await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=999,
                        operator="admin",
                    )


class TestExecuteStageNoBatches:
    """Tests for execute_stage no pending batches (lines 1469-1472)."""

    @pytest.mark.asyncio
    async def test_execute_stage_no_batches_returns_early(self):
        """execute_stage returns early when no pending batches."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with patch.object(
                DefaultPublishService,
                "_get_publish_and_bot_record",
                return_value=(mock_publish, mock_bot),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_get_pending_batches",
                    return_value=(None, []),
                ):
                    result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    assert result.success is True


class TestExecuteStageBotRecordNone:
    """Tests for execute_stage lazy bot_record fetch (lines 1480-1481)."""

    @pytest.mark.asyncio
    async def test_execute_stage_lazy_fetches_bot_record(self):
        """execute_stage fetches bot_record when None is passed."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.extra_config = {}

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

            with patch.object(
                DefaultPublishService,
                "_get_publish_and_bot_record",
                return_value=(mock_publish, None),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_get_pending_batches",
                    return_value=("PROD_FIRST_BATCH", [mock_batch]),
                ):
                    with patch.object(
                        DefaultPublishService,
                        "_execute_batch",
                        new_callable=AsyncMock,
                    ) as mock_exec:
                        from secbaas.community.api.publish_manage import BatchResult

                        mock_exec.return_value = BatchResult(
                            success=True, processed_count=1, failed_count=0
                        )

                        _publish_service_instance._publish_batch_repo = MagicMock()

                        _publish_service_instance._publish_record_repo = MagicMock()
                        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {}

                        # bot_record=None should trigger lazy fetch
                        await _publish_service_instance.execute_stage(
                            tenant="test_tenant",
                            publish_id=1,
                            operator="admin",
                            bot_record=None,
                        )

                        _publish_service_instance._bot_repo.get_by_id.assert_called_once_with(
                            mock_publish.bot_id,
                            tenant="test_tenant",
                            env=mock_env,
                        )


class TestExecuteStageDestroyFailureCleanup:
    """Tests for DESTROY failure cleanup exception handling (lines 1640-1654)."""

    @pytest.mark.asyncio
    async def test_execute_stage_destroy_failure_cleanup_exception(self):
        """DESTROY failure cleanup handles per-device exceptions gracefully."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.publish_type = PublishType.DESTROY.value
        mock_publish.extra_config = {}

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_batch_repo = MagicMock()

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {}

            with patch.object(
                DefaultPublishService,
                "_get_publish_and_bot_record",
                return_value=(mock_publish, mock_bot),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_get_pending_batches",
                    return_value=("PROD_FIRST_BATCH", [mock_batch]),
                ):
                    # Mock _execute_batch to return failure
                    with patch.object(
                        DefaultPublishService,
                        "_execute_batch",
                        new_callable=AsyncMock,
                    ) as mock_exec:
                        from secbaas.community.api.publish_manage import BatchResult

                        mock_exec.return_value = BatchResult(
                            success=False,
                            processed_count=0,
                            failed_count=1,
                        )

                        # Mock the device repo for DESTROY failure cleanup path
                        _publish_service_instance._device_repo = MagicMock()
                        mock_device = MagicMock()
                        mock_device.device_uuid = "dev-1"
                        mock_device.status = DeviceStatus.UPDATING.value
                        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                            mock_device
                        ]
                        # update_status_by_device_uuid raises for first device
                        _publish_service_instance._device_repo.update_status_by_device_uuid.side_effect = RuntimeError(
                            "DB error"
                        )

                        result = await _publish_service_instance.execute_stage(
                            tenant="test_tenant",
                            publish_id=1,
                            operator="admin",
                            publish_record=mock_publish,
                            bot_record=mock_bot,
                        )

                        assert result.success is False


class TestExecuteBatchAllTypes:
    """Tests for _execute_batch type dispatch (lines 1777, 1786, 1796, 1806, 1816)."""

    @pytest.mark.asyncio
    async def test_execute_batch_create_type(self):
        """_execute_batch dispatches to _execute_create_batch for CREATE type."""
        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        with patch.object(
            DefaultPublishService,
            "_execute_create_batch",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MagicMock(success=True)

            await _publish_service_instance._execute_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                publish_type=PublishType.CREATE.value,
                drain_timeout=30,
                batch_repo=MagicMock(),
                operator="admin",
            )

            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_batch_destroy_type(self):
        """_execute_batch dispatches to _execute_destroy_batch for DESTROY type."""
        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        with patch.object(
            DefaultPublishService,
            "_execute_destroy_batch",
            new_callable=AsyncMock,
        ) as mock_destroy:
            mock_destroy.return_value = MagicMock(success=True)

            await _publish_service_instance._execute_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                publish_type=PublishType.DESTROY.value,
                drain_timeout=30,
                batch_repo=MagicMock(),
                operator="admin",
            )

            mock_destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_batch_scale_up_type(self):
        """_execute_batch dispatches to _execute_scale_batch for SCALE_UP type."""
        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1

        with patch.object(
            DefaultPublishService,
            "_execute_scale_batch",
            new_callable=AsyncMock,
        ) as mock_scale:
            mock_scale.return_value = MagicMock(success=True)

            await _publish_service_instance._execute_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                publish_type=PublishType.SCALE_UP.value,
                drain_timeout=30,
                batch_repo=MagicMock(),
                operator="admin",
            )

            mock_scale.assert_called_once()


class TestExecuteCreateBatchLazyFetch:
    """Tests for _execute_create_batch lazy fetch paths (lines 1854-1866)."""

    @pytest.mark.asyncio
    async def test_execute_create_batch_lazy_fetch_bot_not_found(self):
        """_execute_create_batch returns failure when bot not found."""
        mock_batch = MagicMock()
        mock_batch.id = 1
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
            # publish_record is None initially → triggers lazy fetch
            # but we mock _get_publish_and_bot_record via publish_record param
            pass

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = None

            result = await _publish_service_instance._execute_create_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                operator="admin",
                publish_record=mock_publish,
                bot_record=None,
            )

            assert result.success is False
            assert "Bot not found" in str(result.error_message)

    @pytest.mark.asyncio
    async def test_execute_create_batch_lazy_fetch_publish_not_found(self):
        """_execute_create_batch raises PublishNotFoundError when publish not found."""
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
                await _publish_service_instance._execute_create_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    operator="admin",
                    publish_record=None,
                    bot_record=None,
                )


class TestExecuteCreateBatchIdempotent:
    """Tests for _execute_create_batch idempotent guard (lines 1904-1908)."""

    @pytest.mark.asyncio
    async def test_execute_create_batch_skips_existing_processing_record(self):
        """_execute_create_batch skips device with existing CREATED record."""
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
        mock_device.device_uuid = "dev-1"
        mock_device.status = DeviceStatus.PENDING.value

        mock_existing_record = MagicMock()
        mock_existing_record.id = 99
        mock_existing_record.result_status = PublishRecordResult.PROCESSING.value

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
            _publish_service_instance._publish_record_repo.get_by_device_id_and_publish_id.return_value = mock_existing_record

            result = await _publish_service_instance._execute_create_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            # Device was skipped — no insert_record or start_device
            assert result.success is True
            assert result.processed_count == 0
            _publish_service_instance._publish_record_repo.insert_record.assert_not_called()


class TestExecuteUpdateBatchNoDevices:
    """Tests for _execute_update_batch no devices (lines 2111-2118)."""

    @pytest.mark.asyncio
    async def test_execute_update_batch_no_unprocessed_devices(self):
        """_execute_update_batch returns success when no devices to update."""
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

        mock_target_bot = MagicMock()
        mock_target_bot.id = 2
        mock_target_bot.template_uuid = "tpl-1"
        mock_target_bot.extra_config = {}

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "dev-1"
        mock_device.status = DeviceStatus.UPDATING.value  # already updating

        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                mock_device
            ]

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_target_bot

            result = await _publish_service_instance._execute_update_batch(
                tenant="test_tenant",
                publish_id=1,
                batch=mock_batch,
                drain_timeout=30,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            assert result.success is True
            assert result.processed_count == 0


class TestExecuteRestartBatchNotFound:
    """Tests for _execute_restart_batch not-found path (line 2272)."""

    @pytest.mark.asyncio
    async def test_execute_restart_batch_publish_not_found(self):
        """_execute_restart_batch raises PublishNotFoundError."""
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
                await _publish_service_instance._execute_restart_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )
