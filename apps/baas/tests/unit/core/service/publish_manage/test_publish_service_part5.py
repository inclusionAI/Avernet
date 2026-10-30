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


class TestBotFailedStateOnPublishFailure:
    """Tests for bot FAILED state transition on publish failure.

    Per D-01: Only CREATE publish failures transition bot from PENDING to FAILED.
    Other publish types leave bot in its current status.
    """

    @pytest.mark.asyncio
    async def test_create_publish_failure_transitions_bot_to_failed(self):
        """CREATE publish failure should transition bot from PENDING to FAILED."""
        from secbaas.community.api.publish_manage import BatchResult

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.CREATE.value
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = {"auto_complete": True}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.PENDING.value

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PROD_FIRST_BATCH", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_execute_batch",
                    new_callable=AsyncMock,
                    return_value=BatchResult(
                        success=False, processed_count=0, failed_count=1
                    ),
                ):
                    _publish_service_instance._bot_repo = MagicMock()
                    _publish_service_instance._bot_repo.get_by_id.return_value = (
                        mock_bot
                    )

                    result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    # Verify publish transitioned to FAILED
                    assert result.success is False
                    _publish_service_instance._publish_repo.update_status.assert_called()
                    publish_call = (
                        _publish_service_instance._publish_repo.update_status.call_args
                    )
                    assert publish_call.kwargs["status"] == PublishStatus.FAILED.value

                    # Verify bot transitioned to FAILED
                    _publish_service_instance._bot_repo.update_status.assert_called_once()
                    bot_call = (
                        _publish_service_instance._bot_repo.update_status.call_args
                    )
                    assert bot_call.kwargs["status"] == BotStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_update_publish_failure_does_not_transition_bot_status(self):
        """UPDATE publish failure should NOT change bot status (stays ACTIVE)."""
        from secbaas.community.api.publish_manage import BatchResult

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.UPDATE.value
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = {}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.ACTIVE.value

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PROD_FIRST_BATCH", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_execute_batch",
                    new_callable=AsyncMock,
                    return_value=BatchResult(
                        success=False, processed_count=0, failed_count=1
                    ),
                ):
                    _publish_service_instance._bot_repo = MagicMock()
                    _publish_service_instance._bot_repo.get_by_id.return_value = (
                        mock_bot
                    )

                    result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    # Verify publish transitioned to FAILED
                    assert result.success is False

                    # Verify bot was NOT transitioned to FAILED
                    _publish_service_instance._bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_publish_failure_does_not_transition_bot_status(self):
        """RESTART publish failure should NOT change bot status (stays ACTIVE)."""
        from secbaas.community.api.publish_manage import BatchResult

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.RESTART.value
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = {}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.ACTIVE.value

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PROD_FIRST_BATCH", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_execute_batch",
                    new_callable=AsyncMock,
                    return_value=BatchResult(
                        success=False, processed_count=0, failed_count=1
                    ),
                ):
                    _publish_service_instance._bot_repo = MagicMock()
                    _publish_service_instance._bot_repo.get_by_id.return_value = (
                        mock_bot
                    )

                    _result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    # Verify bot was NOT transitioned to FAILED
                    _publish_service_instance._bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_scale_up_publish_failure_does_not_transition_bot_status(self):
        """SCALE_UP publish failure should NOT change bot status (stays ACTIVE)."""
        from secbaas.community.api.publish_manage import BatchResult

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.SCALE_UP.value
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = {}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.ACTIVE.value

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PROD_FIRST_BATCH", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_execute_batch",
                    new_callable=AsyncMock,
                    return_value=BatchResult(
                        success=False, processed_count=0, failed_count=1
                    ),
                ):
                    _publish_service_instance._bot_repo = MagicMock()
                    _publish_service_instance._bot_repo.get_by_id.return_value = (
                        mock_bot
                    )

                    _result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    # Verify bot was NOT transitioned to FAILED
                    _publish_service_instance._bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_scale_down_publish_failure_does_not_transition_bot_status(self):
        """SCALE_DOWN publish failure should NOT change bot status (stays ACTIVE)."""
        from secbaas.community.api.publish_manage import BatchResult

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.SCALE_DOWN.value
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = {}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.ACTIVE.value

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PROD_FIRST_BATCH", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_execute_batch",
                    new_callable=AsyncMock,
                    return_value=BatchResult(
                        success=False, processed_count=0, failed_count=1
                    ),
                ):
                    _publish_service_instance._bot_repo = MagicMock()
                    _publish_service_instance._bot_repo.get_by_id.return_value = (
                        mock_bot
                    )

                    _result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    # Verify bot was NOT transitioned to FAILED
                    _publish_service_instance._bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_publish_failure_does_not_transition_bot_to_failed(self):
        """DESTROY publish failure should keep bot in DESTROYING status."""
        from secbaas.community.api.publish_manage import BatchResult

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.DESTROY.value
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = {}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.DESTROYING.value

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PROD_FIRST_BATCH", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_execute_batch",
                    new_callable=AsyncMock,
                    return_value=BatchResult(
                        success=False, processed_count=0, failed_count=1
                    ),
                ):
                    _publish_service_instance._bot_repo = MagicMock()
                    _publish_service_instance._bot_repo.get_by_id.return_value = (
                        mock_bot
                    )

                    result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    # Verify publish transitioned to FAILED
                    assert result.success is False

                    # Verify bot was NOT transitioned (stays DESTROYING)
                    _publish_service_instance._bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_status_change_handled_safely(self):
        """CREATE failure when bot already DESTROYING should not transition to FAILED.

        Tests idempotent check: only transition if bot.status == PENDING.
        """
        from secbaas.community.api.publish_manage import BatchResult

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.CREATE.value
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = {}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        # Bot already in DESTROYING status (concurrent destroy_bot call)
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.DESTROYING.value

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PROD_FIRST_BATCH", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_execute_batch",
                    new_callable=AsyncMock,
                    return_value=BatchResult(
                        success=False, processed_count=0, failed_count=1
                    ),
                ):
                    _publish_service_instance._bot_repo = MagicMock()
                    _publish_service_instance._bot_repo.get_by_id.return_value = (
                        mock_bot
                    )

                    result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    # Verify publish transitioned to FAILED
                    assert result.success is False

                    # Verify bot was NOT transitioned (stays DESTROYING)
                    # update_status should NOT be called because bot.status != PENDING
                    _publish_service_instance._bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_failure_with_bot_already_failed_is_idempotent(self):
        """CREATE failure when bot already FAILED should not attempt update."""
        from secbaas.community.api.publish_manage import BatchResult

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.CREATE.value
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = {}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 1
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"

        # Bot already in FAILED status
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.FAILED.value

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()

            _publish_service_instance._publish_batch_repo = MagicMock()
            _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
                mock_batch
            ]

            with patch.object(
                DefaultPublishService,
                "_get_pending_batches",
                return_value=("PROD_FIRST_BATCH", [mock_batch]),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_execute_batch",
                    new_callable=AsyncMock,
                    return_value=BatchResult(
                        success=False, processed_count=0, failed_count=1
                    ),
                ):
                    _publish_service_instance._bot_repo = MagicMock()
                    _publish_service_instance._bot_repo.get_by_id.return_value = (
                        mock_bot
                    )

                    _result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    # Verify bot was NOT transitioned (already FAILED)
                    _publish_service_instance._bot_repo.update_status.assert_not_called()


class TestGetActiveSessions:
    """Tests for _get_active_sessions method."""

    @pytest.mark.asyncio
    async def test_get_active_sessions_returns_count(self):
        """Test that _get_active_sessions returns active session count."""
        mock_device = MagicMock()
        mock_device.device_uuid = "dev-uuid-123"

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.return_value = mock_device

        _publish_service_instance._session_repo = MagicMock()
        _publish_service_instance._session_repo.count_active_sessions_by_device.return_value = 3

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            count = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=1
            )

            assert count == 3
            _publish_service_instance._device_repo.get_by_id.assert_called_once_with(
                1, tenant="test-tenant", env="test"
            )
            _publish_service_instance._session_repo.count_active_sessions_by_device.assert_called_once_with(
                device_uuid="dev-uuid-123", tenant="test-tenant"
            )

    @pytest.mark.asyncio
    async def test_get_active_sessions_returns_zero_when_no_device(self):
        """Test that _get_active_sessions returns 0 when device not found."""
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.return_value = (
            None  # Device not found
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            count = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=999
            )

            assert count == 0

    @pytest.mark.asyncio
    async def test_get_active_sessions_handles_exception(self):
        """Test that _get_active_sessions returns 0 on error (graceful degradation)."""
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.side_effect = Exception(
            "Database error"
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            count = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=1
            )

            # Should return 0 on error to allow drain to proceed
            assert count == 0

    @pytest.mark.asyncio
    async def test_get_active_sessions_zero_sessions(self):
        """Test that _get_active_sessions returns 0 when no active sessions."""
        mock_device = MagicMock()
        mock_device.device_uuid = "dev-uuid-456"

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.return_value = mock_device

        _publish_service_instance._session_repo = MagicMock()
        _publish_service_instance._session_repo.count_active_sessions_by_device.return_value = 0

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            count = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=1
            )

            assert count == 0


class TestRetryPublish:
    """Tests for retry_publish method"""

    @pytest.mark.asyncio
    async def test_retry_failed_publish_creates_new_publish(self):
        """retry_publish creates a new PENDING publish from FAILED original."""
        mock_bot = MagicMock()
        mock_bot.id = 1

        # Original FAILED publish
        mock_failed_publish = MagicMock()
        mock_failed_publish.id = 100
        mock_failed_publish.bot_id = 1
        mock_failed_publish.publish_type = "SCALE_UP"
        mock_failed_publish.status = PublishStatus.FAILED.value
        mock_failed_publish.extra_config = {"replica_desired": 5, "batch_capacity": 2}

        # New publish created by retry
        mock_new_publish = MagicMock()
        mock_new_publish.id = 200
        mock_new_publish.bot_id = 1
        mock_new_publish.publish_type = "SCALE_UP"
        mock_new_publish.status = PublishStatus.PENDING.value

        with patch.object(
            DefaultPublishService, "create_publish", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = mock_new_publish

            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_failed_publish
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

            result = await _publish_service_instance.retry_publish(
                tenant="test_tenant",
                publish_id=100,
                operator="test_user",
                request_id="req-123",
            )

            # Verify create_publish was called with original params
            mock_create.assert_called_once_with(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.SCALE_UP,
                operator="test_user",
                request_id="req-123",
                config=PublishConfig(replica_desired=5, batch_capacity=2),
            )

            assert result.id == 200
            assert result.status == PublishStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_retry_non_failed_publish_raises_error(self):
        """retry_publish raises ValueError for non-FAILED status."""

        # Publish in SUCCESS status
        class MockSuccessPublish:
            id = 100
            bot_id = 1
            publish_type = "SCALE_UP"
            status = PublishStatus.SUCCESS.value
            extra_config = {}

        mock_success_publish = MockSuccessPublish()

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_success_publish, MagicMock()),
        ):
            with pytest.raises(ValueError, match="Retry is only valid for FAILED"):
                await _publish_service_instance.retry_publish(
                    tenant="test_tenant",
                    publish_id=100,
                    operator="test_user",
                    request_id="req-123",
                )

    @pytest.mark.asyncio
    async def test_retry_rejected_publish_raises_error(self):
        """retry_publish raises ValueError for REJECTED status."""

        class MockRejectedPublish:
            id = 100
            bot_id = 1
            publish_type = "CREATE"
            status = PublishStatus.REJECTED.value
            extra_config = {}

        mock_rejected_publish = MockRejectedPublish()

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_rejected_publish, MagicMock()),
        ):
            with pytest.raises(ValueError, match="Retry is only valid for FAILED"):
                await _publish_service_instance.retry_publish(
                    tenant="test_tenant",
                    publish_id=100,
                    operator="test_user",
                    request_id="req-123",
                )

    @pytest.mark.asyncio
    async def test_retry_publish_not_found_raises_error(self):
        """retry_publish raises PublishNotFoundError when original not found."""
        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(None, None),
        ):
            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance.retry_publish(
                    tenant="test_tenant",
                    publish_id=999,
                    operator="test_user",
                    request_id="req-123",
                )

    @pytest.mark.asyncio
    async def test_retry_with_custom_config(self):
        """retry_publish uses custom config when provided."""

        class MockFailedPublish:
            id = 100
            bot_id = 1
            publish_type = "SCALE_UP"
            status = PublishStatus.FAILED.value
            extra_config = {"replica_desired": 5, "batch_capacity": 2}

        mock_failed_publish = MockFailedPublish()

        mock_new_publish = MagicMock()
        mock_new_publish.id = 200

        custom_config = PublishConfig(replica_desired=10, batch_capacity=5)

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_failed_publish, MagicMock()),
        ):
            with patch.object(
                DefaultPublishService, "create_publish", new_callable=AsyncMock
            ) as mock_create:
                mock_create.return_value = mock_new_publish

                _result = await _publish_service_instance.retry_publish(
                    tenant="test_tenant",
                    publish_id=100,
                    operator="test_user",
                    request_id="req-123",
                    config=custom_config,
                )

                # Verify custom config was used instead of original
                mock_create.assert_called_once_with(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.SCALE_UP,
                    operator="test_user",
                    request_id="req-123",
                    config=custom_config,
                )
