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


class TestCheckStageAdvancement:
    """Tests for _check_stage_advancement method."""

    @pytest.mark.asyncio
    async def test_check_stage_advancement_stage_failed_create_type(self):
        """_check_stage_advancement sets bot to FAILED on stage failure for CREATE publish."""

        mock_env = MagicMock()

        mock_bot = MagicMock()
        mock_bot.status = BotStatus.PENDING.value

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.CREATE.value
        mock_publish.extra_config = None

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

            mock_batch_repo.return_value = MagicMock()

            await _publish_service_instance._check_stage_advancement(
                tenant="test_tenant",
                publish_id=1,
                current_stage="PROD_FIRST_BATCH",
                stage_failed=True,
            )

            # Verify publish status set to FAILED
            _publish_service_instance._publish_repo.update_status.assert_any_call(
                publish_id=1,
                tenant="test_tenant",
                env=mock_env,
                status=PublishStatus.FAILED.value,
                modifier="callback",
            )

            # Verify bot set to FAILED
            _publish_service_instance._bot_repo.update_status.assert_called_once_with(
                bot_id=1,
                tenant="test_tenant",
                env=mock_env,
                status=BotStatus.FAILED,
                modifier="callback",
            )

    @pytest.mark.asyncio
    async def test_check_stage_advancement_stage_failed_ignores_bot_not_pending(self):
        """_check_stage_advancement does not set bot FAILED if bot is not PENDING."""

        mock_env = MagicMock()

        mock_bot = MagicMock()
        mock_bot.status = BotStatus.ACTIVE.value  # Not PENDING

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.CREATE.value
        mock_publish.extra_config = None

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

            mock_batch_repo.return_value = MagicMock()

            await _publish_service_instance._check_stage_advancement(
                tenant="test_tenant",
                publish_id=1,
                current_stage="PROD_FIRST_BATCH",
                stage_failed=True,
            )

            # Bot should NOT be updated since it's not PENDING
            _publish_service_instance._bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_stage_advancement_not_all_complete_returns_early(self):
        """_check_stage_advancement returns early if not all batches are complete."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = None

        mock_pending_batch = MagicMock()
        mock_pending_batch.stage = "PROD_FIRST_BATCH"
        mock_pending_batch.status = "PENDING"

        mock_completed_batch = MagicMock()
        mock_completed_batch.stage = "PROD_FIRST_BATCH"
        mock_completed_batch.status = "COMPLETED"

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
                mock_pending_batch,
                mock_completed_batch,
            ]

            # Should not raise or proceed further
            await _publish_service_instance._check_stage_advancement(
                tenant="test_tenant",
                publish_id=1,
                current_stage="PROD_FIRST_BATCH",
                stage_failed=False,
            )

            # publish status should NOT be updated
            _publish_service_instance._publish_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_stage_advancement_all_stages_complete_no_auto_complete(self):
        """_check_stage_advancement logs when all stages complete without auto_complete."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"auto_complete": False}

        mock_completed_batch = MagicMock()
        mock_completed_batch.stage = "PROD_OTHER_BATCH"
        mock_completed_batch.status = "COMPLETED"

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
                mock_completed_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=(None, []),
            ):
                await _publish_service_instance._check_stage_advancement(
                    tenant="test_tenant",
                    publish_id=1,
                    current_stage="PROD_OTHER_BATCH",
                    stage_failed=False,
                )

                # Should NOT auto-complete because auto_complete=False
                _publish_service_instance._publish_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_stage_advancement_pause_for_approval(self):
        """_check_stage_advancement transitions to APPROVING when pause_for_approval is set."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = {
            "stages": {
                "PREPUB": {"pause_for_approval": True},
                "PROD_FIRST_BATCH": {"pause_for_approval": True},
            }
        }

        mock_completed_batch = MagicMock()
        mock_completed_batch.stage = "PREPUB"
        mock_completed_batch.status = "COMPLETED"

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
                mock_completed_batch
            ]

            next_stage = "PROD_FIRST_BATCH"
            mock_next_batch = MagicMock()
            mock_next_batch.batch_capacity = 5

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=(next_stage, [mock_next_batch]),
            ):
                await _publish_service_instance._check_stage_advancement(
                    tenant="test_tenant",
                    publish_id=1,
                    current_stage="PREPUB",
                    stage_failed=False,
                )

                _publish_service_instance._publish_repo.update_status.assert_called_once_with(
                    publish_id=1,
                    tenant="test_tenant",
                    env=mock_env,
                    status=PublishStatus.APPROVING.value,
                    modifier="callback",
                )

    @pytest.mark.asyncio
    async def test_check_stage_advancement_empty_batches_completed_directly(self):
        """_check_stage_advancement completes empty batches directly and recurses."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.extra_config = None
        mock_publish.publish_type = PublishType.RESTART.value

        mock_completed_batch = MagicMock()
        mock_completed_batch.stage = "PROD_FIRST_BATCH"
        mock_completed_batch.status = "COMPLETED"

        mock_empty_batch = MagicMock()
        mock_empty_batch.id = 200
        mock_empty_batch.batch_capacity = 0

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
                mock_completed_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                side_effect=[
                    ("PROD_OTHER_BATCH", [mock_empty_batch]),
                    (None, []),  # No more stages on recursive call
                ],
            ):
                with patch(
                    "secbaas.community.core.service.publish_manage._publish_service.is_paas_mock_mode",
                    return_value=False,
                ):
                    # Execute real method — it will auto-progress through empty batches
                    # then try execute_stage or complete_publish
                    with patch.object(
                        DefaultPublishService,
                        "complete_publish",
                        new_callable=AsyncMock,
                    ):
                        await _publish_service_instance._check_stage_advancement(
                            tenant="test_tenant",
                            publish_id=1,
                            current_stage="PROD_FIRST_BATCH",
                            stage_failed=False,
                        )

                        # Empty batch should be marked COMPLETED
                        _publish_service_instance._publish_batch_repo.update_status.assert_any_call(
                            batch_id=200,
                            tenant="test_tenant",
                            env=mock_env,
                            status=BatchStatus.COMPLETED.value,
                            modifier="callback",
                        )


class TestGetDeviceDetails:
    """Tests for _get_device_details static method."""

    def test_get_device_details_resolves_device_uuids(self):
        """_get_device_details fetches and maps device details across batches."""
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_record = MagicMock()
        mock_record.device_id = 10
        mock_record.event_type = "CREATE"
        mock_record.result_status = PublishRecordResult.SUCCESS.value
        mock_record.result_message = "OK"
        mock_record.gmt_create = datetime.now()

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "COMPLETED"

        mock_record_repo = MagicMock()
        mock_record_repo.list_by_batch_id.return_value = [mock_record]

        mock_device = MagicMock()
        mock_device.device_uuid = "uuid-10"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=MagicMock(),
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                10: mock_device
            }

            device_details, failed_devices = (
                _publish_service_instance._get_device_details(
                    batches=[mock_batch],
                    tenant="test_tenant",
                    record_repo=mock_record_repo,
                )
            )

            assert len(device_details) == 1
            assert device_details[0].batch_id == 1
            assert len(device_details[0].devices) == 1
            assert device_details[0].devices[0].device_uuid == "uuid-10"
            assert len(failed_devices) == 0

    def test_get_device_details_tracks_failed_devices(self):
        """_get_device_details tracks devices with FAILED result_status."""
        from secbaas.community.api.publish_manage import PublishRecordResult

        mock_failed_record = MagicMock()
        mock_failed_record.device_id = 11
        mock_failed_record.event_type = "RESTART"
        mock_failed_record.result_status = PublishRecordResult.FAILED.value
        mock_failed_record.result_message = "Restart failed"
        mock_failed_record.gmt_create = datetime.now()

        mock_batch = MagicMock()
        mock_batch.id = 2
        mock_batch.batch_index = 1
        mock_batch.stage = "PROD_OTHER_BATCH"
        mock_batch.status = "FAILED"

        mock_record_repo = MagicMock()
        mock_record_repo.list_by_batch_id.return_value = [mock_failed_record]

        mock_device = MagicMock()
        mock_device.device_uuid = "uuid-11"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=MagicMock(),
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.get_by_ids.return_value = {
                11: mock_device
            }

            device_details, failed_devices = (
                _publish_service_instance._get_device_details(
                    batches=[mock_batch],
                    tenant="test_tenant",
                    record_repo=mock_record_repo,
                )
            )

            assert len(device_details) == 1
            assert len(failed_devices) == 1
            assert failed_devices[0].device_id == 11
            assert failed_devices[0].result_status == PublishRecordResult.FAILED.value

    def test_get_device_details_empty_batches(self):
        """_get_device_details handles empty batch list."""
        mock_record_repo = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=MagicMock(),
        ):
            device_details, failed_devices = (
                _publish_service_instance._get_device_details(
                    batches=[],
                    tenant="test_tenant",
                    record_repo=mock_record_repo,
                )
            )

            assert device_details == []
            assert failed_devices == []


class TestCompletePublishUpdateTransfer:
    """Tests for complete_publish UPDATE publish device transfer logic."""

    @pytest.mark.asyncio
    async def test_complete_publish_update_transfers_device_rels(self):
        """complete_publish for UPDATE transfers device relationships to new bot."""

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.UPDATE.value
        mock_publish.extra_config = {"target_bot_id": 2}
        mock_publish.creator = "admin"
        mock_publish.modifier = "admin"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()
        mock_publish.status = PublishStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        mock_rel = MagicMock()
        mock_rel.id = 100
        mock_rel.device_uuid = "device-uuid-1"
        mock_rel.domain = "test_domain"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=MagicMock(),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            mock_record_repo.return_value = MagicMock()

            _publish_service_instance._bot_repo = MagicMock()

            mock_rel_repo_instance = MagicMock()
            mock_rel_repo_instance.list_by_bot_id.return_value = [mock_rel]
            _publish_service_instance._rel_repo = mock_rel_repo_instance

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_by_publish_id.return_value = []

            # Wire _publish_repo.get_by_id to return mock_publish
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            result = await _publish_service_instance.complete_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            assert result.status == PublishStatus.SUCCESS.value
            # Verify device transfer called
            _publish_service_instance._bot_repo.complete_update_transfer.assert_called_once_with(
                old_bot_id=1,
                new_bot_id=2,
                device_uuids=["device-uuid-1"],
                domain="test_domain",
                tenant="test_tenant",
                env=ANY,
                modifier="admin",
            )

    @pytest.mark.asyncio
    async def test_complete_publish_update_no_device_rels_marks_target_failed(self):
        """complete_publish UPDATE with no device rels marks target bot FAILED."""

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.UPDATE.value
        mock_publish.extra_config = {"target_bot_id": 2}  # plain dict
        mock_publish.creator = "admin"
        mock_publish.modifier = "admin"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()
        mock_publish.status = PublishStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        # For _refresh_publish_response, the publish repo must return a publish
        # with extra_config as a plain dict
        mock_fresh_publish = MagicMock()
        mock_fresh_publish.id = 1
        mock_fresh_publish.bot_id = 1
        mock_fresh_publish.publish_type = PublishType.UPDATE.value
        mock_fresh_publish.extra_config = {"target_bot_id": 2}
        mock_fresh_publish.creator = "admin"
        mock_fresh_publish.modifier = "admin"
        mock_fresh_publish.gmt_create = datetime.now()
        mock_fresh_publish.gmt_modified = datetime.now()
        mock_fresh_publish.status = PublishStatus.SUCCESS.value

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=MagicMock(),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_fresh_publish
            )

            mock_record_repo.return_value = MagicMock()

            _publish_service_instance._bot_repo = MagicMock()

            mock_rel_repo_instance = MagicMock()
            mock_rel_repo_instance.list_by_bot_id.return_value = []

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_by_publish_id.return_value = []

            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="SUCCESS",
            ):
                result = await _publish_service_instance.complete_publish(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="admin",
                    publish_record=mock_publish,
                    bot_record=mock_bot,
                )

                # Target bot should be set to FAILED
                _publish_service_instance._bot_repo.update_status.assert_called_once_with(
                    bot_id=2,
                    tenant="test_tenant",
                    env=ANY,
                    status=BotStatus.FAILED,
                    modifier="admin",
                )
                # Old bot should be completed_destroy
                _publish_service_instance._bot_repo.complete_destroy.assert_called_once()

                assert result.status == PublishStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_complete_publish_update_no_target_bot_id_fallback(self):
        """complete_publish UPDATE without target_bot_id sets old bot ACTIVE."""

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.UPDATE.value
        mock_publish.extra_config = {}  # No target_bot_id
        mock_publish.creator = "admin"
        mock_publish.modifier = "admin"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()
        mock_publish.status = PublishStatus.ACTIVE.value

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=MagicMock(),
        ):
            mock_publish_repo.return_value = MagicMock()

            mock_record_repo.return_value = MagicMock()

            _publish_service_instance._bot_repo = MagicMock()

            _publish_service_instance._publish_record_repo = MagicMock()
            _publish_service_instance._publish_record_repo.list_by_publish_id.return_value = []

            result = await _publish_service_instance.complete_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                publish_record=mock_publish,
                bot_record=mock_bot,
            )

            # Old bot should be set ACTIVE
            _publish_service_instance._bot_repo.update_status.assert_called_once_with(
                bot_id=1,
                tenant="test_tenant",
                env=ANY,
                status=BotStatus.ACTIVE,
                modifier="admin",
            )

            assert result.status == PublishStatus.SUCCESS.value
