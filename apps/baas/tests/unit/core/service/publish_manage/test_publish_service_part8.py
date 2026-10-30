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


class TestListPublishes:
    """Tests for list_publishes method."""

    @pytest.mark.asyncio
    async def test_list_publishes_with_bot_id_filter(self):
        """list_publishes filters by bot_id when provided."""

        mock_env = MagicMock()

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish1 = MagicMock()
        mock_publish1.id = 1
        mock_publish1.bot_id = 1
        mock_publish1.publish_type = "CREATE"
        mock_publish1.status = "ACTIVE"
        mock_publish1.extra_config = None
        mock_publish1.creator = "user1"
        mock_publish1.modifier = "user1"
        mock_publish1.gmt_create = datetime.now()
        mock_publish1.gmt_modified = datetime.now()

        mock_publish2 = MagicMock()
        mock_publish2.id = 2
        mock_publish2.bot_id = 1
        mock_publish2.publish_type = "RESTART"
        mock_publish2.status = "PENDING"
        mock_publish2.extra_config = None
        mock_publish2.creator = "user1"
        mock_publish2.modifier = "user1"
        mock_publish2.gmt_create = datetime.now()
        mock_publish2.gmt_modified = datetime.now()

        mock_records = [mock_publish1, mock_publish2]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.list_by_bot_id.return_value = (
                mock_records
            )

            _publish_service_instance._bot_service.get_bot = AsyncMock(
                return_value=mock_bot
            )
            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="PROD_FIRST_BATCH",
            ):
                results = await _publish_service_instance.list_publishes(
                    tenant="test_tenant",
                    bot_id=1,
                    page=1,
                    page_size=20,
                )

                assert len(results) == 2
                assert results[0].id == 1
                assert results[1].id == 2

    @pytest.mark.asyncio
    async def test_list_publishes_skips_bot_not_found(self):
        """list_publishes skips publishing when bot is not found."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 999
        mock_publish.publish_type = "CREATE"
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = None
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_records = [mock_publish]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.list_by_bot_id.return_value = (
                mock_records
            )

            _publish_service_instance._bot_service.get_bot = AsyncMock(
                return_value=None
            )
            results = await _publish_service_instance.list_publishes(
                tenant="test_tenant",
                bot_id=999,
                page=1,
                page_size=20,
            )

            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_list_publishes_status_filter(self):
        """list_publishes filters by status when provided."""
        mock_env = MagicMock()

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_active = MagicMock()
        mock_publish_active.id = 1
        mock_publish_active.bot_id = 1
        mock_publish_active.publish_type = "CREATE"
        mock_publish_active.status = "ACTIVE"
        mock_publish_active.extra_config = None
        mock_publish_active.creator = "user1"
        mock_publish_active.modifier = "user1"
        mock_publish_active.gmt_create = datetime.now()
        mock_publish_active.gmt_modified = datetime.now()

        mock_publish_pending = MagicMock()
        mock_publish_pending.id = 2
        mock_publish_pending.bot_id = 1
        mock_publish_pending.publish_type = "RESTART"
        mock_publish_pending.status = "PENDING"
        mock_publish_pending.extra_config = None
        mock_publish_pending.creator = "user1"
        mock_publish_pending.modifier = "user1"
        mock_publish_pending.gmt_create = datetime.now()
        mock_publish_pending.gmt_modified = datetime.now()

        mock_records = [mock_publish_active, mock_publish_pending]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.list_by_bot_id.return_value = (
                mock_records
            )

            _publish_service_instance._bot_service.get_bot = AsyncMock(
                return_value=mock_bot
            )
            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="PROD_FIRST_BATCH",
            ):
                results = await _publish_service_instance.list_publishes(
                    tenant="test_tenant",
                    bot_id=1,
                    status=PublishStatus.ACTIVE,
                    page=1,
                    page_size=20,
                )

                assert len(results) == 1
                assert results[0].status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_list_publishes_no_bot_id_returns_all(self):
        """list_publishes returns empty list when no bot_id is given."""
        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.list_by_bot_id.return_value = []

            results = await _publish_service_instance.list_publishes(
                tenant="test_tenant",
                bot_id=None,
                page=1,
                page_size=20,
            )

            assert results == []

    @pytest.mark.asyncio
    async def test_list_publishes_pagination(self):
        """list_publishes applies pagination correctly."""
        mock_env = MagicMock()

        mock_bot = MagicMock()
        mock_bot.id = 1

        # Create 5 mock records
        mock_records = []
        for i in range(5):
            mp = MagicMock()
            mp.id = i + 1
            mp.bot_id = 1
            mp.publish_type = "CREATE"
            mp.status = "ACTIVE"
            mp.extra_config = None
            mp.creator = "user1"
            mp.modifier = "user1"
            mp.gmt_create = datetime.now()
            mp.gmt_modified = datetime.now()
            mock_records.append(mp)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.list_by_bot_id.return_value = (
                mock_records
            )

            _publish_service_instance._bot_service.get_bot = AsyncMock(
                return_value=mock_bot
            )
            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="PROD_FIRST_BATCH",
            ):
                # Page 1, size 2 — should get first 2
                results = await _publish_service_instance.list_publishes(
                    tenant="test_tenant",
                    bot_id=1,
                    page=1,
                    page_size=2,
                )
                assert len(results) == 2
                assert results[0].id == 1

                # Page 2, size 2 — should get next 2
                results = await _publish_service_instance.list_publishes(
                    tenant="test_tenant",
                    bot_id=1,
                    page=2,
                    page_size=2,
                )
                assert len(results) == 2
                assert results[0].id == 3

                # Page 3, size 2 — should get last 1
                results = await _publish_service_instance.list_publishes(
                    tenant="test_tenant",
                    bot_id=1,
                    page=3,
                    page_size=2,
                )
                assert len(results) == 1


class TestExecuteBatchUnknownType:
    """Tests for _execute_batch unknown/unsupported publish type."""

    @pytest.mark.asyncio
    async def test_execute_batch_unknown_publish_type(self):
        """_execute_batch returns failed BatchResult for unknown publish type."""
        mock_batch = MagicMock()
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 5

        mock_batch_repo = MagicMock()

        result = await _publish_service_instance._execute_batch(
            tenant="test_tenant",
            publish_id=1,
            batch=mock_batch,
            publish_type="UNKNOWN_TYPE",
            drain_timeout=30,
            batch_repo=mock_batch_repo,
            operator="admin",
        )

        assert result.success is False
        assert result.processed_count == 0
        assert result.failed_count == 5
        assert "Unknown publish type" in (result.error_message or "")


class TestExecuteDestroyBatch:
    """Tests for _execute_destroy_batch method."""

    @pytest.mark.asyncio
    async def test_execute_destroy_batch_success(self):
        """DESTROY batch drains and destroys active devices successfully."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            PublishRecordResult,
        )

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 5

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-destroy"
        mock_device.domain = "test_domain"
        mock_device.provider_device_id = "provider-1"
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        mock_drain = DrainResult(
            success=True,
            sessions_remaining=0,
            duration_seconds=2.0,
            timeout_reached=False,
        )
        mock_destroy_response = MagicMock()
        mock_destroy_response.success = True
        mock_destroy_response.error_message = None
        mock_destroy_response.hook_result = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
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
                mock_pending_record = MagicMock()
                mock_pending_record.id = 42
                mock_pending_record.device_id = mock_device.id
                mock_pending_record.device_uuid = mock_device.device_uuid

                _publish_service_instance._publish_record_repo = MagicMock()
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    mock_pending_record
                ]

                _publish_service_instance._device_service.destroy_device_by_uuid = (
                    AsyncMock(
                        new_callable=AsyncMock, return_value=mock_destroy_response
                    )
                )
                mock_rel = MagicMock()
                mock_rel.id = 100
                mock_rel.device_uuid = "device-uuid-destroy"
                mock_rel_repo_instance = MagicMock()
                mock_rel_repo_instance.list_by_bot_id.return_value = [mock_rel]
                _publish_service_instance._rel_repo = mock_rel_repo_instance

                result = await _publish_service_instance._execute_destroy_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )

                assert result.success is True
                assert result.processed_count == 1
                assert result.failed_count == 0

                # Verify record lifecycle: PENDING -> CREATED -> SUCCESS
                update_calls = _publish_service_instance._publish_record_repo.update_result.call_args_list
                # First call: CREATED, last call: SUCCESS
                assert (
                    update_calls[0].kwargs["result_status"]
                    == PublishRecordResult.PROCESSING.value
                )
                assert (
                    update_calls[-1].kwargs["result_status"]
                    == PublishRecordResult.SUCCESS.value
                )

                # Verify relationship soft-deleted
                mock_rel_repo_instance.soft_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_destroy_batch_device_destroy_failure(self):
        """DESTROY batch handles device destruction failure gracefully."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            PublishRecordResult,
        )

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 5

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-destroy"
        mock_device.domain = "test_domain"
        mock_device.provider_device_id = "provider-1"
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        mock_drain = DrainResult(
            success=True,
            sessions_remaining=0,
            duration_seconds=2.0,
            timeout_reached=False,
        )
        mock_destroy_response = MagicMock()
        mock_destroy_response.success = False
        mock_destroy_response.error_message = "Destruction failed"
        mock_destroy_response.hook_result = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
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
                mock_pending_record = MagicMock()
                mock_pending_record.id = 42
                mock_pending_record.device_id = mock_device.id
                mock_pending_record.device_uuid = mock_device.device_uuid

                _publish_service_instance._publish_record_repo = MagicMock()
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    mock_pending_record
                ]

                _publish_service_instance._device_service.destroy_device_by_uuid = (
                    AsyncMock(
                        new_callable=AsyncMock, return_value=mock_destroy_response
                    )
                )
                result = await _publish_service_instance._execute_destroy_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )

                assert result.success is False
                assert result.processed_count == 0
                assert result.failed_count == 1

                update_calls = _publish_service_instance._publish_record_repo.update_result.call_args_list
                # Last call should be FAILED after CREATED transition
                assert (
                    update_calls[-1].kwargs["result_status"]
                    == PublishRecordResult.FAILED.value
                )

    @pytest.mark.asyncio
    async def test_execute_destroy_batch_exception_handling(self):
        """DESTROY batch handles exceptions during device destruction."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            PublishRecordResult,
        )

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 5

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.device_uuid = "device-uuid-destroy"
        mock_device.domain = "test_domain"
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        mock_drain = DrainResult(
            success=True,
            sessions_remaining=0,
            duration_seconds=2.0,
            timeout_reached=False,
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
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
                mock_pending_record = MagicMock()
                mock_pending_record.id = 42
                mock_pending_record.device_id = mock_device.id
                mock_pending_record.device_uuid = mock_device.device_uuid

                _publish_service_instance._publish_record_repo = MagicMock()
                _publish_service_instance._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
                    mock_pending_record
                ]

                _publish_service_instance._device_service.destroy_device_by_uuid = (
                    AsyncMock(
                        new_callable=AsyncMock,
                        side_effect=RuntimeError("Unexpected error"),
                    )
                )
                result = await _publish_service_instance._execute_destroy_batch(
                    tenant="test_tenant",
                    publish_id=1,
                    batch=mock_batch,
                    drain_timeout=30,
                    operator="admin",
                )

                assert result.success is False
                assert result.failed_count == 1

                update_calls = _publish_service_instance._publish_record_repo.update_result.call_args_list
                assert (
                    update_calls[-1].kwargs["result_status"]
                    == PublishRecordResult.FAILED.value
                )
