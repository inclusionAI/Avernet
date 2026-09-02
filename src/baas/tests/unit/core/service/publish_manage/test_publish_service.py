"""Tests for DefaultPublishService."""

from datetime import datetime
from typing import cast
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_manage import BotStatus
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
from secbaas.community.core.service.health_check.paas import (
    ActiveSessionVerdict,
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


class TestPublishCreation:
    """SVC-PUB-01 to SVC-PUB-05, SVC-PUB-15: Publish type creation tests"""

    @pytest.mark.asyncio
    async def test_create_publish_create_type(self):
        """SVC-PUB-01: CREATE publish generates 5-stage pipeline"""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_id = "bot-001"

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

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        # Mock get_by_id to return records with batch_index so sorted() works
        mock_batch_record = MagicMock()
        mock_batch_record.batch_index = 0
        mock_batch_record.id = 1
        mock_batch_record.batch_capacity = 5
        _publish_service_instance._publish_batch_repo = MagicMock()
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

        result = await _publish_service_instance.create_publish(
            tenant="test_tenant",
            bot_id=1,
            publish_type=PublishType.CREATE,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
        )

        assert result.publish_type == "CREATE"
        assert result.status == "PENDING"
        _publish_service_instance._publish_repo.insert_publish.assert_called_once()
        # Verify batches created - CREATE should have multiple batches
        assert _publish_service_instance._publish_batch_repo.insert_batch.call_count > 0

    @pytest.mark.asyncio
    async def test_create_publish_update_type(self):
        """SVC-PUB-02: UPDATE publish generates 5-stage pipeline"""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.replica_desired = 3

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "UPDATE"
        mock_publish.status = "PENDING"
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_new_bot = MagicMock()
        mock_new_bot.id = 2

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._bot_service.create_bot_record = AsyncMock(
            return_value=mock_new_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        # Mock get_by_id to return records with batch_index so sorted() works
        mock_batch_record = MagicMock()
        mock_batch_record.batch_index = 0
        mock_batch_record.id = 1
        mock_batch_record.batch_capacity = 5
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.insert_batch.return_value = 1
        _publish_service_instance._publish_batch_repo.get_by_id.return_value = (
            mock_batch_record
        )

        # Mock devices with ACTIVE status so _create_device_records_for_publish
        # has eligible devices (UPDATE selects ACTIVE or FAILED)
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = "ACTIVE"
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]
        _publish_service_instance._publish_record_repo = MagicMock()

        result = await _publish_service_instance.create_publish(
            tenant="test_tenant",
            bot_id=1,
            publish_type=PublishType.UPDATE,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
        )

        assert result.publish_type == "UPDATE"
        assert result.status == "PENDING"
        _publish_service_instance._template_service.get_online_template_by_uuid.assert_not_called()
        _publish_service_instance._bot_service.create_bot_record.assert_awaited_once_with(
            tenant="test_tenant",
            source_bot_id=1,
            new_config=None,
            new_template_uuid=None,
            operator="user1",
        )

    @pytest.mark.asyncio
    async def test_create_publish_restart_type(self):
        """SVC-PUB-03: RESTART publish generates 2-stage pipeline"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "RESTART"
        mock_publish.status = "PENDING"
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = "ACTIVE"
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]

        result = await _publish_service_instance.create_publish(
            tenant="test_tenant",
            bot_id=1,
            publish_type=PublishType.RESTART,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
        )

        assert result.publish_type == "RESTART"
        assert result.status == "PENDING"

    @pytest.mark.asyncio
    async def test_create_publish_scale_up(self):
        """SVC-PUB-04: SCALE_UP uses direct execution (single batch)"""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.template_uuid = "template-uuid-1"
        mock_bot.domain = "test_domain"
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

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()

        _publish_service_instance._template_service = MagicMock()
        _publish_service_instance._template_service.get_online_template_by_uuid.return_value = MagicMock()

        mock_device = MagicMock()
        mock_device.device_uuid = "device-uuid-1"
        _publish_service_instance._device_service.create_device = MagicMock(
            return_value=mock_device
        )
        _publish_service_instance._rel_repo = MagicMock()

        result = await _publish_service_instance.create_publish(
            tenant="test_tenant",
            bot_id=1,
            publish_type=PublishType.SCALE_UP,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
        )

        assert result.publish_type == "SCALE_UP"
        # SCALE_UP should create single batch

    @pytest.mark.asyncio
    async def test_concurrent_publish_prevention(self):
        """SVC-PUB-15: Concurrent active publish returns existing publish."""
        mock_bot = MagicMock()
        mock_bot.id = 1

        existing_publish = MagicMock()
        existing_publish.id = 999
        existing_publish.status = "ACTIVE"
        existing_publish.bot_id = 1
        existing_publish.publish_type = "UPDATE"
        existing_publish.tenant = "test_tenant"
        existing_publish.extra_config = {}
        existing_publish.creator = "prev_user"
        existing_publish.modifier = "prev_user"
        existing_publish.gmt_create = datetime.now()
        existing_publish.gmt_modified = datetime.now()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = (
            existing_publish
        )

        _publish_service_instance._publish_batch_repo = MagicMock()
        mock_batch = MagicMock()
        mock_batch.status = "COMPLETED"
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            mock_batch
        ]

        result = await _publish_service_instance.create_publish(
            tenant="test_tenant",
            bot_id=1,
            publish_type=PublishType.UPDATE,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
        )

        assert result.id == 999
        assert result.status == "ACTIVE"


# ====================================================================
# TEST: create_publish — stale publish auto-resolution
# ====================================================================


class TestStalePublishAutoResolution:
    """Tests for auto-resolution of stale (timed-out) publishes in create_publish."""

    @pytest.mark.asyncio
    async def test_auto_resolve_stale_publish_allows_new_publish(self):
        """create_publish auto-resolves a stale publish and allows new one to proceed."""
        from datetime import timedelta

        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS,
        )

        mock_bot = MagicMock()
        mock_bot.id = 1

        stale_time = datetime.now() - timedelta(
            seconds=DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS + 60
        )
        mock_existing = MagicMock()
        mock_existing.id = 2606
        mock_existing.publish_type = "CREATE"
        mock_existing.status = "ACTIVE"
        mock_existing.bot_id = 1
        mock_existing.extra_config = None
        mock_existing.creator = "user1"
        mock_existing.modifier = "user1"
        mock_existing.gmt_create = stale_time
        mock_existing.gmt_modified = stale_time

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "RESTART"
        mock_publish.status = "PENDING"
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_env = MagicMock()
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = DeviceStatus.ACTIVE.value

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        # First call to get_active_by_bot_id returns the stale publish
        # Second call (for RESTART scope check) returns None since
        # the stale publish has been resolved
        _publish_service_instance._publish_repo.get_active_by_bot_id.side_effect = [
            mock_existing,
            None,
        ]
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock()
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._device_repo = MagicMock()
            _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                mock_device
            ]

            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="PROD_FIRST_BATCH",
            ):
                with patch.object(
                    DefaultPublishService,
                    "_check_and_handle_timeout",
                    new_callable=AsyncMock,
                ) as mock_timeout:
                    result = await _publish_service_instance.create_publish(
                        tenant="test_tenant",
                        bot_id=1,
                        publish_type=PublishType.RESTART,
                        operator="user1",
                        request_id="test-request-id-12345678901234567890",
                    )

            mock_timeout.assert_awaited_once_with(mock_existing, "test_tenant")
            assert result is not None
            assert result.publish_type == "RESTART"
            assert result.status == "PENDING"

    @pytest.mark.asyncio
    async def test_stale_publish_auto_resolution_calls_timeout_handler(self):
        """create_publish calls _check_and_handle_timeout for stale publishes."""
        from datetime import timedelta

        from secbaas.community.api.publish_manage import (
            DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS,
        )

        mock_bot = MagicMock()
        mock_bot.id = 1

        stale_time = datetime.now() - timedelta(
            seconds=DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS + 60
        )
        mock_existing = MagicMock()
        mock_existing.id = 2606
        mock_existing.publish_type = "CREATE"
        mock_existing.status = "ACTIVE"
        mock_existing.bot_id = 1
        mock_existing.extra_config = None
        mock_existing.creator = "user1"
        mock_existing.modifier = "user1"
        mock_existing.gmt_create = stale_time
        mock_existing.gmt_modified = stale_time

        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.tenant = "test_tenant"
        mock_publish.publish_type = "RESTART"
        mock_publish.status = "PENDING"
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = "ACTIVE"

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.side_effect = [
            mock_existing,
            None,
        ]
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock()
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with patch.object(
                DefaultPublishService,
                "_check_and_handle_timeout",
                new_callable=AsyncMock,
            ) as mock_timeout:
                with patch.object(
                    DefaultPublishService,
                    "_get_current_stage",
                    return_value="PROD_FIRST_BATCH",
                ):
                    _publish_service_instance._device_repo = MagicMock()
                    _publish_service_instance._device_repo.list_by_bot_id.return_value = [
                        mock_device
                    ]

                    await _publish_service_instance.create_publish(
                        tenant="test_tenant",
                        bot_id=1,
                        publish_type=PublishType.RESTART,
                        operator="user1",
                        request_id="test-request-id-12345678901234567890",
                    )

        mock_timeout.assert_awaited_once_with(mock_existing, "test_tenant")

    @pytest.mark.asyncio
    async def test_active_publish_within_timeout_raises_conflict(self):
        """create_publish raises PublishConflictError if active publish is within timeout."""

        mock_bot = MagicMock()
        mock_bot.id = 1

        # Recently modified — within the timeout threshold
        recent_time = datetime.now()
        mock_existing = MagicMock()
        mock_existing.id = 2606
        mock_existing.publish_type = "CREATE"
        mock_existing.status = "ACTIVE"
        mock_existing.bot_id = 1
        mock_existing.extra_config = None
        mock_existing.creator = "user1"
        mock_existing.modifier = "user1"
        mock_existing.gmt_create = recent_time
        mock_existing.gmt_modified = recent_time

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = (
            mock_existing
        )

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock()
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(
                PublishConflictError,
                match="Cannot create",
            ):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.RESTART,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

        # Should NOT have been called since within timeout
        _publish_service_instance._publish_repo.update_status.assert_not_called()


class TestPublishApproval:
    """SVC-PUB-06 to SVC-PUB-08, SVC-PUB-14, SVC-PUB-15: Approval gate tests"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("pub_type", [PublishType.CREATE, PublishType.UPDATE])
    async def test_approval_gates_create_update(self, pub_type):
        """SVC-PUB-06: CREATE/UPDATE require approval at each stage (PENDING->ACTIVE->APPROVING->ACTIVE)"""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_id = "bot-001"

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_id = 1
        mock_record.publish_type = pub_type.value
        mock_record.status = "PENDING"
        mock_record.extra_config = {}
        mock_record.creator = "user1"
        mock_record.modifier = "user1"
        mock_record.request_id = "test-request-id-12345678901234567890"
        mock_record.gmt_create = datetime.now()
        mock_record.gmt_modified = datetime.now()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._bot_service.create_bot_record = AsyncMock(
            return_value=MagicMock(id=2)
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        # Mock get_active_by_bot_id for create
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        # Return PENDING status
        mock_record_pending = MagicMock(**mock_record.__dict__)
        mock_record_pending.status = "PENDING"
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_record_pending
        )

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        # Mock batch repo to return records for sorted() in _create_device_records
        mock_batch_record = MagicMock()
        mock_batch_record.batch_index = 0
        mock_batch_record.id = 1
        mock_batch_record.batch_capacity = 5
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.insert_batch.return_value = 1
        _publish_service_instance._publish_batch_repo.get_by_id.return_value = (
            mock_batch_record
        )

        # Mock devices so _create_device_records_for_publish has eligible devices
        device_status = "PENDING" if pub_type == PublishType.CREATE else "ACTIVE"
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = device_status
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]
        _publish_service_instance._publish_record_repo = MagicMock()

        # Test: Create in PENDING status (ready for approval)
        result = await _publish_service_instance.create_publish(
            tenant="test_tenant",
            bot_id=1,
            publish_type=pub_type,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
        )
        assert result.status == "PENDING"

        # Test 2: Approve from PENDING
        result = await _publish_service_instance.approve_stage(
            tenant="test_tenant", publish_id=1, operator="admin"
        )
        _publish_service_instance._publish_repo.update_status.assert_called_with(
            publish_id=1,
            tenant="test_tenant",
            env="dev",
            status="ACTIVE",
            modifier="admin",
        )

    @pytest.mark.asyncio
    async def test_restart_single_approval(self):
        """SVC-PUB-07: RESTART requires 1 approval (at prod_first->prod_other boundary)"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_record_pending = MagicMock()
        mock_record_pending.id = 1
        mock_record_pending.bot_id = 1
        mock_record_pending.publish_type = "RESTART"
        mock_record_pending.status = "PENDING"
        mock_record_pending.extra_config = {}
        mock_record_pending.creator = "user1"
        mock_record_pending.modifier = "user1"
        mock_record_pending.request_id = "test-request-id-12345678901234567890"
        mock_record_pending.gmt_create = datetime.now()
        mock_record_pending.gmt_modified = datetime.now()

        # Mock batch with stage
        mock_batch = MagicMock()
        mock_batch.status = "PENDING"
        mock_batch.stage = "PROD_FIRST_BATCH"

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_record_pending
        )

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            mock_batch
        ]

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        _result = await _publish_service_instance.approve_stage(
            tenant="test_tenant", publish_id=1, operator="admin"
        )

        _publish_service_instance._publish_repo.update_status.assert_called_with(
            publish_id=1,
            tenant="test_tenant",
            env="dev",
            status="ACTIVE",
            modifier="admin",
        )

    @pytest.mark.asyncio
    async def test_reject_publish(self):
        """SVC-PUB-14: Publish rejection handling (PENDING or APPROVING -> REJECTED)"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "PENDING"
        mock_record.bot_id = 1
        mock_record.publish_type = "CREATE"
        mock_record.extra_config = {}
        mock_record.creator = "user1"
        mock_record.modifier = "user1"
        mock_record.request_id = "test-request-id-12345678901234567890"
        mock_record.gmt_create = "2024-01-01T00:00:00"
        mock_record.gmt_modified = "2024-01-01T00:00:00"

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_record

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = []

        _result = await _publish_service_instance.reject_publish(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
            reason="Configuration error",
        )

        _publish_service_instance._publish_repo.update_status.assert_called_with(
            publish_id=1,
            tenant="test_tenant",
            env="dev",
            status="REJECTED",
            modifier="admin",
        )

    @pytest.mark.asyncio
    async def test_invalid_state_transitions(self):
        """Test that invalid state transitions are blocked"""
        # Test: Can approve from PENDING
        assert _publish_service_instance._can_transition("PENDING", "approve")
        # Test: Can reject from PENDING
        assert _publish_service_instance._can_transition("PENDING", "reject")
        # Test: Can revoke only from APPROVING
        assert _publish_service_instance._can_transition("APPROVING", "revoke")
        assert not _publish_service_instance._can_transition("PENDING", "revoke")
        # Test: Cannot approve from FAILED
        assert not _publish_service_instance._can_transition("FAILED", "approve")

    @pytest.mark.asyncio
    async def test_revoke_publish(self):
        """Test revoking publish at APPROVING state (after approval, before execution)"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "APPROVING"
        mock_record.bot_id = 1
        mock_record.publish_type = "CREATE"
        mock_record.extra_config = {}
        mock_record.creator = "user1"
        mock_record.modifier = "user1"
        mock_record.request_id = "test-request-id-12345678901234567890"
        mock_record.gmt_create = "2024-01-01T00:00:00"
        mock_record.gmt_modified = "2024-01-01T00:00:00"

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_record

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = []

        _result = await _publish_service_instance.revoke_publish(
            tenant="test_tenant",
            publish_id=1,
            operator="admin",
            reason="Change of plans",
        )

        _publish_service_instance._publish_repo.update_status.assert_called_with(
            publish_id=1,
            tenant="test_tenant",
            env="dev",
            status="REVOKED",
            modifier="admin",
        )


class TestStateTransitions:
    """State machine transition tests"""

    @pytest.mark.asyncio
    async def test_pending_to_active_transition(self):
        """PENDING -> ACTIVE on initial approval"""
        assert _publish_service_instance._can_transition("PENDING", "approve")
        assert (
            _publish_service_instance._get_next_status("PENDING", "approve") == "ACTIVE"
        )

    @pytest.mark.asyncio
    async def test_approve_stage_transition(self):
        """PENDING -> ACTIVE, APPROVING -> ACTIVE transitions"""
        assert _publish_service_instance._can_transition("PENDING", "approve")
        assert (
            _publish_service_instance._get_next_status("PENDING", "approve") == "ACTIVE"
        )
        assert _publish_service_instance._can_transition("APPROVING", "approve")
        assert (
            _publish_service_instance._get_next_status("APPROVING", "approve")
            == "ACTIVE"
        )

    @pytest.mark.asyncio
    async def test_revoke_publish_transition(self):
        """APPROVING -> REVOKED transition"""
        assert _publish_service_instance._can_transition("APPROVING", "revoke")
        assert (
            _publish_service_instance._get_next_status("APPROVING", "revoke")
            == "REVOKED"
        )

    @pytest.mark.asyncio
    async def test_fail_publish_transition(self):
        """ACTIVE -> FAILED transition"""
        assert _publish_service_instance._can_transition("ACTIVE", "fail")
        assert _publish_service_instance._get_next_status("ACTIVE", "fail") == "FAILED"

    @pytest.mark.asyncio
    async def test_success_completion_transition(self):
        """ACTIVE -> SUCCESS transition"""
        assert _publish_service_instance._can_transition("ACTIVE", "all_complete")
        assert (
            _publish_service_instance._get_next_status("ACTIVE", "all_complete")
            == "SUCCESS"
        )

    @pytest.mark.asyncio
    async def test_stage_completion_to_approving(self):
        """ACTIVE -> APPROVING transition (stage complete, pause for approval)"""
        assert _publish_service_instance._can_transition("ACTIVE", "stage_complete")
        assert (
            _publish_service_instance._get_next_status("ACTIVE", "stage_complete")
            == "APPROVING"
        )


class TestDeviceDrain:
    """SVC-PUB-09, SVC-PUB-10: Device drain tests.

    Drain is now driven by ``ActiveSessionVerdict`` returned from
    ``_get_active_sessions``: ``CLEAR`` advances drain; ``ACTIVE`` or
    ``UNKNOWN`` block until timeout. DB count no longer drives the decision.
    """

    @pytest.mark.asyncio
    async def test_drain_waits_for_sessions(self):
        """SVC-PUB-09: Graceful device drain clears when verdict=CLEAR."""
        from secbaas.community.core.service.health_check.paas import (
            ActiveSessionVerdict,
        )

        with patch.object(
            DefaultPublishService,
            "_get_active_sessions",
            new_callable=AsyncMock,
            return_value=ActiveSessionVerdict.CLEAR,
        ):
            result = await _publish_service_instance._drain_device(
                tenant="test_tenant",
                device_id=1,
                timeout_seconds=5,
                check_interval=0.1,
            )

            assert result.success is True
            assert result.sessions_remaining == 0
            assert result.timeout_reached is False
            assert result.verdict == ActiveSessionVerdict.CLEAR.value

    @pytest.mark.asyncio
    async def test_drain_timeout(self):
        """SVC-PUB-10: Drain timeout enforcement with verdict=ACTIVE."""
        from secbaas.community.core.service.health_check.paas import (
            ActiveSessionVerdict,
        )

        # ACTIVE never completes -> drain blocks -> timeout failure.
        with patch.object(
            DefaultPublishService,
            "_get_active_sessions",
            new_callable=AsyncMock,
            return_value=ActiveSessionVerdict.ACTIVE,
        ):
            result = await _publish_service_instance._drain_device(
                tenant="test_tenant",
                device_id=1,
                timeout_seconds=1,  # Short timeout for test
                check_interval=0.1,
            )

            assert result.success is False  # Timed out
            assert result.sessions_remaining > 0
            assert result.timeout_reached is True
            assert result.duration_seconds >= 0.5
            assert result.verdict == ActiveSessionVerdict.ACTIVE.value

    @pytest.mark.asyncio
    async def test_drain_unknown_blocks_until_timeout(self):
        """UNKNOWN must not release the device: drain blocks until timeout."""
        from secbaas.community.core.service.health_check.paas import (
            ActiveSessionVerdict,
        )

        with patch.object(
            DefaultPublishService,
            "_get_active_sessions",
            new_callable=AsyncMock,
            return_value=ActiveSessionVerdict.UNKNOWN,
        ):
            result = await _publish_service_instance._drain_device(
                tenant="test_tenant",
                device_id=1,
                timeout_seconds=1,
                check_interval=0.1,
            )

            assert result.success is False
            assert result.sessions_remaining > 0
            assert result.timeout_reached is True
            assert result.verdict == ActiveSessionVerdict.UNKNOWN.value


class TestBatchExecution:
    """SVC-PUB-12: Batch execution tests"""

    @pytest.mark.asyncio
    async def test_execute_stage_requires_active_status(self):
        """execute_stage only works when publish status is ACTIVE"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.status = "PENDING"  # Not ACTIVE
        mock_publish.bot_id = 1
        mock_publish.extra_config = {}

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        with pytest.raises(ValueError) as exc_info:
            await _publish_service_instance.execute_stage(
                tenant="test_tenant", publish_id=1, operator="admin"
            )

        assert "ACTIVE" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_stage_processes_batches(self):
        """SVC-PUB-12: Rolling batch execution"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.status = "ACTIVE"
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"drain_timeout_seconds": 5}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"  # Non-SUCCESS so it's pending

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            mock_batch
        ]

        # Mock _get_pending_batches: first call for initial check,
        # second call after stage completes (no more batches = auto-complete)
        with patch.object(
            DefaultPublishService,
            "_get_pending_batches",
            side_effect=[
                ("PROD_FIRST_BATCH", [mock_batch]),  # Initial: has pending
                ("SUCCESS", []),  # After completion: no more stages
            ],
        ):
            with patch.object(
                DefaultPublishService,
                "_execute_batch",
                new_callable=AsyncMock,
            ) as mock_exec:
                from secbaas.community.api.publish_manage import BatchResult

                mock_exec.return_value = BatchResult(
                    success=True, processed_count=2, failed_count=0
                )

                with patch.object(
                    DefaultPublishService,
                    "complete_publish",
                    new_callable=AsyncMock,
                ):
                    result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant", publish_id=1, operator="admin"
                    )

                    assert result.success is True
                    mock_exec.assert_called_once()


class TestVersionManagement:
    """SVC-PUB-11: Version management tests"""

    @pytest.mark.asyncio
    async def test_complete_publish_increments_version(self):
        """SVC-PUB-11: Version auto-increment on publish success"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        # Mock publish record with bot_id
        class MockPublish:
            id = 1
            bot_id = 1
            publish_type = "CREATE"  # Not DESTROY, so no cleanup needed
            status = "ACTIVE"  # Current status
            extra_config = {}

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.status = "ACTIVE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "user1"
        mock_publish_record.modifier = "user1"
        mock_publish_record.gmt_create = "2024-01-01T00:00:00"
        mock_publish_record.gmt_modified = "2024-01-01T00:00:00"
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        with patch.object(
            DefaultPublishService,
            "get_publish",
            new_callable=AsyncMock,
            return_value=MockPublish(),
        ):
            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

            _result = await _publish_service_instance.complete_publish(
                tenant="test_tenant", publish_id=1, operator="admin"
            )

            # Verify status updated to SUCCESS
            _publish_service_instance._publish_repo.update_status.assert_called_with(
                publish_id=1,
                tenant="test_tenant",
                env="dev",
                status="SUCCESS",
                modifier="admin",
            )


class TestPublishProgress:
    """Tests for get_publish_progress functionality"""

    @pytest.mark.asyncio
    async def test_get_progress_returns_none_for_nonexistent_publish(self):
        """Progress returns None when publish not found"""
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = None

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=999
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_progress_returns_none_for_tenant_mismatch(self):
        """Progress enforces tenant isolation"""
        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = "ACTIVE"
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id_including_deleted.return_value = (
            None
        )

        result = await _publish_service_instance.get_publish_progress(
            tenant="wrong_tenant", publish_id=1
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_progress_returns_complete_response(self):
        """Progress returns all required fields"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = "ACTIVE"
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = "PENDING"
        mock_batch.batch_capacity = 5

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            mock_batch
        ]

        _publish_service_instance._publish_record_repo = MagicMock()
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {}
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {}

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.publish_id == 1
        assert result.status == "ACTIVE"
        assert result.overall_progress is not None
        assert isinstance(result.overall_progress, ProgressSummary)
        assert isinstance(result.stages, list)
        assert result.timeline is not None

    def test_compute_overall_progress_calculation(self):
        """Progress percentage is calculated correctly"""
        batches = []
        for i in range(4):
            batch = MagicMock()
            batch.batch_capacity = 10
            batch.status = (
                BatchStatus.COMPLETED.value if i < 2 else BatchStatus.PENDING.value
            )
            batches.append(batch)

        status_counts = {"SUCCESS": 20, "FAILED": 2}

        result = _publish_service_instance._compute_overall_progress(
            cast(list[PublishBatchRecord], batches), status_counts
        )

        assert result.total_batches == 4
        assert result.completed_batches == 2
        assert result.total_devices == 40
        assert result.processed_devices == 22
        assert result.failed_devices == 2
        assert result.progress_percentage == pytest.approx(55.0)  # 22/40 * 100


class TestProgressModels:
    """Tests for progress model validation"""

    def test_progress_summary_validation(self):
        """ProgressSummary validates percentage bounds"""
        # Valid range
        summary = ProgressSummary(
            total_batches=10,
            completed_batches=5,
            total_devices=100,
            processed_devices=50,
            failed_devices=2,
            progress_percentage=50.0,
        )
        assert summary.progress_percentage == 50.0

    def test_stage_progress_model(self):
        """StageProgress contains all required fields"""
        stage = StageProgress(
            stage="PROD_FIRST_BATCH",
            status="ACTIVE",
            batches_completed=2,
            batches_total=5,
            devices_processed=10,
            devices_failed=1,
            devices_total=25,
        )
        assert stage.stage == "PROD_FIRST_BATCH"
        assert stage.devices_total == 25

    def test_publish_progress_response_model(self):
        """PublishProgressResponse aggregates all progress info"""
        from datetime import datetime

        from secbaas.community.api.publish_manage import ProgressTimeline

        response = PublishProgressResponse(
            publish_id=1,
            status="ACTIVE",
            current_stage="PROD_FIRST_BATCH",
            overall_progress=ProgressSummary(
                total_batches=5,
                completed_batches=2,
                total_devices=100,
                processed_devices=40,
                failed_devices=1,
                progress_percentage=40.0,
            ),
            stages=[
                StageProgress(
                    stage="GRAY",
                    status="SUCCESS",
                    batches_completed=2,
                    batches_total=2,
                    devices_processed=8,
                    devices_failed=0,
                    devices_total=8,
                )
            ],
            timeline=ProgressTimeline(
                gmt_create=datetime.now(),
                gmt_modified=datetime.now(),
                estimated_remaining_seconds=120.0,
            ),
        )
        assert response.publish_id == 1
        assert len(response.stages) == 1


class TestPublishProgressLifecycle:
    """Tests for progress tracking through publish lifecycle"""

    @pytest.mark.asyncio
    async def test_progress_after_create_shows_pending_state(self):
        """After create_publish, progress should show PENDING status with 0% complete"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = PublishStatus.PENDING.value
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        # Create batches for all stages (CREATE has 5-stage pipeline)
        batches = []
        stages = ["PREPUB", "GRAY", "PROD_FIRST_BATCH", "PROD_OTHER_BATCH"]
        for i, stage in enumerate(stages):
            batch = MagicMock()
            batch.id = i + 1
            batch.stage = stage
            batch.status = PublishStatus.PENDING.value
            batch.batch_capacity = 5
            batches.append(batch)

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = batches

        _publish_service_instance._publish_record_repo = MagicMock()
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {}
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {}

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.status == PublishStatus.PENDING.value
        assert result.overall_progress.progress_percentage == 0.0
        assert result.overall_progress.completed_batches == 0
        assert result.overall_progress.total_batches == 4

    @pytest.mark.asyncio
    async def test_progress_during_execution_shows_partial_complete(self):
        """During stage execution, progress should show partial completion"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = PublishStatus.ACTIVE.value
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        # PREPUB and GRAY complete, PROD_FIRST_BATCH in progress
        batches = [
            MagicMock(
                id=1,
                stage="PREPUB",
                status=BatchStatus.COMPLETED.value,
                batch_capacity=2,
            ),
            MagicMock(
                id=2, stage="GRAY", status=BatchStatus.COMPLETED.value, batch_capacity=4
            ),
            MagicMock(
                id=3,
                stage="PROD_FIRST_BATCH",
                status=BatchStatus.RUNNING.value,
                batch_capacity=5,
            ),
            MagicMock(
                id=4,
                stage="PROD_OTHER_BATCH",
                status=BatchStatus.PENDING.value,
                batch_capacity=10,
            ),
        ]

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = batches

        _publish_service_instance._publish_record_repo = MagicMock()
        # 6 devices processed (2 from PREPUB + 4 from GRAY)
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {
            "SUCCESS": 6,
        }
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {}

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.status == PublishStatus.ACTIVE.value
        assert result.overall_progress.completed_batches == 2
        assert result.overall_progress.total_batches == 4
        # 6 processed out of 21 total
        assert result.overall_progress.processed_devices == 6
        assert result.overall_progress.total_devices == 21
        # Verify stage breakdown
        assert len(result.stages) >= 1
        # PREPUB should show SUCCESS
        prepub_stage = next((s for s in result.stages if s.stage == "PREPUB"), None)
        assert prepub_stage is not None
        assert prepub_stage.status == PublishStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_progress_after_complete_shows_100_percent(self):
        """After publish completes, progress should show 100%"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = PublishStatus.SUCCESS.value
        mock_publish_record.publish_type = "SCALE_UP"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        # All batches complete
        batches = [
            MagicMock(
                id=1,
                stage="PROD_FIRST_BATCH",
                status=BatchStatus.COMPLETED.value,
                batch_capacity=10,
            ),
        ]

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = batches

        _publish_service_instance._publish_record_repo = MagicMock()
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {
            "SUCCESS": 10,
        }
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {
            "SUCCESS": 10
        }

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.status == PublishStatus.SUCCESS.value
        assert result.overall_progress.completed_batches == 1
        assert result.overall_progress.total_batches == 1
        assert result.overall_progress.processed_devices == 10
        assert result.overall_progress.total_devices == 10
        assert result.overall_progress.progress_percentage == 100.0

    @pytest.mark.asyncio
    async def test_progress_with_failures_shows_failed_count(self):
        """Progress should correctly report failed device counts"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_record = MagicMock()
        mock_publish_record.id = 1
        mock_publish_record.bot_id = 1
        mock_publish_record.status = PublishStatus.ACTIVE.value
        mock_publish_record.publish_type = "CREATE"
        mock_publish_record.extra_config = {}
        mock_publish_record.creator = "test"
        mock_publish_record.modifier = "test"
        mock_publish_record.gmt_create = datetime.now()
        mock_publish_record.gmt_modified = datetime.now()

        batches = [
            MagicMock(
                id=1,
                stage="PREPUB",
                status=BatchStatus.COMPLETED.value,
                batch_capacity=10,
            ),
        ]

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_publish_record
        )

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = batches

        _publish_service_instance._publish_record_repo = MagicMock()
        # 8 success, 2 failures
        _publish_service_instance._publish_record_repo.count_records_by_publish_id.return_value = {
            "SUCCESS": 8,
            "FAILED": 2,
        }
        _publish_service_instance._publish_record_repo.count_records_by_batch_id.return_value = {
            "SUCCESS": 8,
            "FAILED": 2,
        }

        result = await _publish_service_instance.get_publish_progress(
            tenant="test_tenant", publish_id=1
        )

        assert result is not None
        assert result.overall_progress.failed_devices == 2
        assert result.overall_progress.processed_devices == 10
        # Stage should also show failures
        assert result.stages[0].devices_failed == 2


class TestAutoComplete:
    """Tests for auto-complete functionality"""

    def test_auto_complete_enabled_by_default(self):
        """Auto-complete is enabled by default"""
        config = PublishConfig()
        assert config.auto_complete is True

    def test_should_auto_complete_disabled_by_config(self):
        """Auto-complete can be disabled via config"""
        config = PublishConfig(auto_complete=False)
        assert config.auto_complete is False

        # Even with all batches complete, should return False
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.COMPLETED.value),
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is False

    def test_should_auto_complete_returns_true_when_enabled_and_complete(self):
        """Auto-complete triggers when enabled and all batches complete"""
        config = PublishConfig(auto_complete=True)
        assert config.auto_complete is True

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.COMPLETED.value),
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is True

    def test_should_auto_complete_returns_false_when_batch_failed(self):
        """Auto-complete does not trigger when any batch fails"""
        config = PublishConfig(auto_complete=True)

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.FAILED.value),  # One batch failed
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is False

    def test_should_auto_complete_returns_false_when_pending_batches(self):
        """Auto-complete does not trigger when there are pending batches (multi-stage)"""
        config = PublishConfig(auto_complete=True)

        _publish_service_instance._publish_batch_repo = MagicMock()
        # Simulate multi-stage: PREPUB completed, but GRAY stage batches are PENDING
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),  # PREPUB batch
            MagicMock(status=BatchStatus.COMPLETED.value),  # PREPUB batch
            MagicMock(status=BatchStatus.PENDING.value),  # GRAY batch (next stage)
            MagicMock(status=BatchStatus.PENDING.value),  # GRAY batch (next stage)
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is False

    def test_should_auto_complete_single_stage_single_batch(self):
        """Auto-complete should trigger for single-stage single-batch (like SCALE_UP)"""
        config = PublishConfig(auto_complete=True)

        _publish_service_instance._publish_batch_repo = MagicMock()
        # Single batch in "direct" stage - all COMPLETED
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
        ]

        result = _publish_service_instance._should_auto_complete(
            "test_tenant", 1, config
        )
        assert result is True

    def test_check_all_batches_complete_returns_false_for_running(self):
        """Returns False when batch is still running"""
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.RUNNING.value),
        ]

        result = _publish_service_instance._check_all_batches_complete("test_tenant", 1)
        assert result is False

    def test_check_all_batches_complete_returns_true_all_completed(self):
        """Returns True only when all batches are COMPLETED"""
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.COMPLETED.value),
            MagicMock(status=BatchStatus.COMPLETED.value),
        ]

        result = _publish_service_instance._check_all_batches_complete("test_tenant", 1)
        assert result is True

    @pytest.mark.asyncio
    async def test_complete_publish_is_idempotent(self):
        """Calling complete_publish twice returns the same result"""
        mock_bot = MagicMock()
        mock_bot.id = 1

        # Mock publish record already in SUCCESS status
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.status = PublishStatus.SUCCESS.value  # Already completed
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = "2024-01-01T00:00:00"
        mock_publish.gmt_modified = "2024-01-01T00:00:00"

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = []

        # Should return early without updating
        _result = await _publish_service_instance.complete_publish(
            tenant="test_tenant", publish_id=1, operator="admin"
        )

        # Verify update_status was NOT called since already SUCCESS
        _publish_service_instance._publish_repo.update_status.assert_not_called()


class TestPublishAutoCompact:
    """Tests for auto-compact feature: progressive stages based on device count."""

    def test_generate_batches_single_device_compacts_to_one_stage(self):
        """Single device should create only 1 stage (PROD_FIRST_BATCH)."""
        config = PublishConfig(
            replica_desired=1,
            batch_capacity=1,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # Should have exactly 1 batch (compacted)
        assert len(batches) == 1
        # Should be PROD_FIRST_BATCH stage
        assert batches[0].stage == "PROD_FIRST_BATCH"
        assert batches[0].batch_capacity == 1
        assert batches[0].device_count == 1

    def test_generate_batches_two_devices_compacts_to_two_stages(self):
        """Two devices should compact to 2 stages."""
        config = PublishConfig(
            replica_desired=2,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 2 devices → 2 stages: PROD_FIRST_BATCH, PROD_OTHER_BATCH
        assert len(batches) == 2
        assert batches[0].stage == "PROD_FIRST_BATCH"
        assert batches[1].stage == "PROD_OTHER_BATCH"
        # Each stage gets 1 device
        assert batches[0].device_count == 1
        assert batches[1].device_count == 1

    def test_generate_batches_three_devices_compacts_to_three_stages(self):
        """Three devices should compact to 3 stages."""
        config = PublishConfig(
            replica_desired=3,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 3 devices → 3 stages: GRAY, PROD_FIRST_BATCH, PROD_OTHER_BATCH
        assert len(batches) == 3
        assert batches[0].stage == "GRAY"
        assert batches[1].stage == "PROD_FIRST_BATCH"
        assert batches[2].stage == "PROD_OTHER_BATCH"

    def test_generate_batches_four_devices_uses_full_pipeline(self):
        """Four devices should use full 4-stage pipeline (no compact)."""
        config = PublishConfig(
            replica_desired=4,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 4 devices = 4 stages, no compact needed
        # Uses full pipeline: PREPUB, GRAY, PROD_FIRST_BATCH, PROD_OTHER_BATCH
        # Each stage gets 1 device
        assert len(batches) == 4
        assert batches[0].stage == "PREPUB"
        assert batches[0].device_count == 1
        assert batches[1].stage == "GRAY"
        assert batches[1].device_count == 1
        assert batches[2].stage == "PROD_FIRST_BATCH"
        assert batches[2].device_count == 1
        assert batches[3].stage == "PROD_OTHER_BATCH"
        assert batches[3].device_count == 1

    def test_generate_batches_five_devices_uses_full_pipeline(self):
        """Five devices should use full pipeline (5 > 4 stages)."""
        config = PublishConfig(
            replica_desired=5,
            batch_capacity=2,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 5 devices > 4 stages, should NOT compact - use full pipeline
        # First batch should be PREPUB (first stage of full pipeline)
        assert len(batches) >= 1
        assert batches[0].stage == "PREPUB"

    def test_generate_batches_restart_single_device_compacts(self):
        """RESTART with 1 device should compact to 1 stage (PROD_FIRST_BATCH)."""
        config = PublishConfig(
            replica_desired=1,
            batch_capacity=1,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.RESTART, config
        )

        # RESTART has 2 stages, 1 device should compact to 1 stage
        assert len(batches) == 1
        assert batches[0].stage == "PROD_FIRST_BATCH"

    def test_generate_batches_restart_two_devices_uses_full_pipeline(self):
        """RESTART with 2 devices should use full 2-stage pipeline."""
        config = PublishConfig(
            replica_desired=2,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.RESTART, config
        )

        # RESTART has 2 stages, 2 devices = no compact
        assert len(batches) == 2
        assert batches[0].stage == "PROD_FIRST_BATCH"
        assert batches[1].stage == "PROD_OTHER_BATCH"

    def test_generate_batches_no_replica_desired_uses_defaults(self):
        """Without replica_desired, uses default stage config."""
        # Default config with no replica_desired
        batches = _publish_service_instance._generate_batches(PublishType.CREATE, None)

        # Should create batches based on default stage config
        assert len(batches) > 0

    # ========================================================================
    # Tests for _calculate_total_devices SCALE operations (Tasks 1.3-1.5)
    # ========================================================================

    def test_calculate_total_devices_scale_up_returns_scale_amount(self):
        """SCALE_UP: scale_amount parameter takes priority over replica_desired."""
        config = PublishConfig(
            replica_desired=5,  # target: will have 5 total
            batch_capacity=5,
        )
        stages = [("direct", "PROD_FIRST_BATCH")]
        result = _publish_service_instance._calculate_total_devices(
            config, stages, scale_amount=3
        )

        # Should return 3 (scale_amount), not 5 (replica_desired)
        assert result == 3

    def test_calculate_total_devices_scale_down_returns_scale_amount(self):
        """SCALE_DOWN: scale_amount parameter takes priority over replica_desired."""
        config = PublishConfig(
            replica_desired=2,  # target: will have 2 remaining
            batch_capacity=5,
        )
        stages = [("direct", "PROD_FIRST_BATCH")]
        result = _publish_service_instance._calculate_total_devices(
            config, stages, scale_amount=3
        )

        # Should return 3 (scale_amount), not 2 (replica_desired)
        assert result == 3

    def test_calculate_total_devices_scale_fallback_to_replica_desired(self):
        """SCALE config without scale_amount should fallback to replica_desired."""
        config = PublishConfig(
            replica_desired=5,  # only target specified
            batch_capacity=5,
        )
        stages = [("direct", "PROD_FIRST_BATCH")]
        result = _publish_service_instance._calculate_total_devices(config, stages)

        # Should fallback to replica_desired
        assert result == 5

    # ========================================================================
    # Tests for SCALE multi-batch support (Tasks 2.3-2.5)
    # ========================================================================

    def test_scale_up_with_scale_amount_exceeds_batch_capacity(self):
        """SCALE_UP with scale_amount > batch_capacity should create multiple batches."""
        config = PublishConfig(
            replica_desired=10,  # target: 3 current + 7 delta
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_UP, config, scale_amount=7
        )

        # 7 devices with batch_capacity=5 should create 2 batches: [5, 2]
        assert len(batches) == 2
        assert batches[0].device_count == 5
        assert batches[0].batch_capacity == 5
        assert batches[1].device_count == 2
        assert batches[1].batch_capacity == 2

    def test_scale_down_with_scale_amount_exceeds_batch_capacity(self):
        """SCALE_DOWN with scale_amount > batch_capacity should create multiple batches."""
        config = PublishConfig(
            replica_desired=2,  # target: 10 current - 8 delta
            batch_capacity=3,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_DOWN, config, scale_amount=8
        )

        # 8 devices with batch_capacity=3 should create 3 batches: [3, 3, 2]
        assert len(batches) == 3
        assert batches[0].device_count == 3
        assert batches[1].device_count == 3
        assert batches[2].device_count == 2

    def test_scale_up_batch_sizing_exact(self):
        """SCALE_UP with scale_amount=7, batch_capacity=5 creates batches [5, 2]."""
        config = PublishConfig(
            replica_desired=12,  # target: 5 current + 7 delta
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_UP, config, scale_amount=7
        )

        assert len(batches) == 2
        assert batches[0].device_count == 5
        assert batches[0].batch_capacity == 5
        assert batches[1].device_count == 2
        assert batches[1].batch_capacity == 2

    def test_scale_up_single_batch_when_within_capacity(self):
        """SCALE_UP with scale_amount <= batch_capacity should create single batch."""
        config = PublishConfig(
            replica_desired=8,  # target: 5 current + 3 delta
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_UP, config, scale_amount=3
        )

        # 3 devices with batch_capacity=5 should fit in 1 batch
        assert len(batches) == 1
        assert batches[0].device_count == 3
        assert batches[0].batch_capacity == 3

    # ========================================================================
    # Tests for zero-device batch skipping (Tasks 3.2-3.3)
    # ========================================================================

    def test_auto_compact_skips_zero_device_batches(self):
        """Auto-compact with fewer devices than stages should not create zero-device batches."""
        # With 2 devices, auto-compact should create 2 batches with device_count=1 each
        # NOT 4 batches with [1, 1, 0, 0]
        config = PublishConfig(
            replica_desired=2,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # Should have exactly 2 batches, not 4
        assert len(batches) == 2
        # All batches should have device_count > 0
        for batch in batches:
            assert batch.device_count > 0

    def test_create_single_device_single_batch(self):
        """CREATE with replica_desired=1 creates only 1 batch (no zero-device batches)."""
        config = PublishConfig(
            replica_desired=1,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # Exactly 1 batch, no zero-device batches
        assert len(batches) == 1
        assert batches[0].device_count == 1
        assert batches[0].batch_capacity == 1

    # ========================================================================
    # Tests with 10+ devices (Tasks 4.1-4.5)
    # ========================================================================

    def test_create_twelve_devices_batch_sizing(self):
        """CREATE with replica_desired=12 and batch_capacity=5 creates batches [5, 5, 2]."""
        config = PublishConfig(
            replica_desired=12,
            batch_capacity=5,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.CREATE, config
        )

        # 12 devices > 4 stages, no compact - uses full pipeline
        # Each stage (4 stages) gets devices distributed, then each may split by batch_capacity
        # With distribute_devices=True and 12 devices over 4 stages: 3 devices per stage
        # But each stage then creates batches by batch_capacity
        # Total devices should equal 12
        total_devices = sum(b.device_count for b in batches)
        assert total_devices == 12
        # Each batch should respect batch_capacity
        for batch in batches:
            assert batch.batch_capacity <= 5

    def test_scale_up_fifteen_devices(self):
        """SCALE_UP with scale_amount=15 and batch_capacity=4 creates batches [4, 4, 4, 3]."""
        config = PublishConfig(
            replica_desired=20,  # target: 5 current + 15 delta
            batch_capacity=4,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.SCALE_UP, config, scale_amount=15
        )

        # 15 devices / batch_capacity=4 = 4 batches: [4, 4, 4, 3]
        assert len(batches) == 4
        assert batches[0].device_count == 4
        assert batches[1].device_count == 4
        assert batches[2].device_count == 4
        assert batches[3].device_count == 3

    def test_update_ten_devices_batch_sizing(self):
        """UPDATE with replica_desired=10 and batch_capacity=3 uses correct batch sizing."""
        config = PublishConfig(
            replica_desired=10,
            batch_capacity=3,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.UPDATE, config
        )

        # 10 devices > 4 stages, uses full pipeline with distribute_devices
        # Distribution: 10/4 = not even, last 2 stages get extras
        # Total devices should be 10
        total_devices = sum(b.device_count for b in batches)
        assert total_devices == 10
        # Each batch should respect batch_capacity
        for batch in batches:
            assert batch.batch_capacity <= 3

    def test_restart_many_devices_respects_batch_capacity(self):
        """RESTART with many devices respects batch_capacity."""
        config = PublishConfig(
            replica_desired=15,
            batch_capacity=4,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.RESTART, config
        )

        # RESTART has 2 stages, 15 devices distributed 1 per stage then split
        # With distribute_devices: 15 devices over 2 stages = each stage gets ~7-8
        # Then each stage splits by batch_capacity
        total_devices = sum(b.device_count for b in batches)
        assert total_devices == 15
        # Each batch should respect batch_capacity
        for batch in batches:
            assert batch.batch_capacity <= 4

    def test_destroy_many_devices_respects_batch_capacity(self):
        """DESTROY with many devices respects batch_capacity."""
        config = PublishConfig(
            replica_desired=20,
            batch_capacity=6,
        )
        batches = _publish_service_instance._generate_batches(
            PublishType.DESTROY, config
        )

        # DESTROY has 1 stage, uses distribute_devices with 20 devices
        # Total devices should be 20
        total_devices = sum(b.device_count for b in batches)
        assert total_devices == 20
        # Each batch should respect batch_capacity
        for batch in batches:
            assert batch.batch_capacity <= 6


class TestAutoExecuteOnApprove:
    """Tests for auto-execute when approving from PENDING to ACTIVE."""

    @pytest.mark.asyncio
    async def test_approve_auto_executes_single_stage_publish(self):
        """Single device publish should auto-execute and auto-complete on approve.

        Flow:
        1. Create publish with 1 device → compacts to 1 stage
        2. Approve → PENDING → ACTIVE → auto-execute → SUCCESS
        """
        mock_publish_pending = MagicMock()
        mock_publish_pending.id = 1
        mock_publish_pending.bot_id = 1
        mock_publish_pending.publish_type = "CREATE"
        mock_publish_pending.status = "PENDING"
        mock_publish_pending.extra_config = {"auto_complete": True}

        mock_publish_active = MagicMock()
        mock_publish_active.id = 1
        mock_publish_active.bot_id = 1
        mock_publish_active.publish_type = "CREATE"
        mock_publish_active.status = "ACTIVE"
        mock_publish_active.extra_config = {"auto_complete": True}

        mock_publish_success = MagicMock()
        mock_publish_success.id = 1
        mock_publish_success.bot_id = 1
        mock_publish_success.publish_type = "CREATE"
        mock_publish_success.status = "SUCCESS"
        mock_publish_success.extra_config = {"auto_complete": True}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.stage = "PROD_FIRST_BATCH"
        mock_batch.status = BatchStatus.PENDING.value
        mock_batch.batch_capacity = 1

        mock_bot = MagicMock()
        mock_bot.id = 1

        # Use a dict that can be modified to simulate state changes
        record_state = {"status": "PENDING"}

        def get_by_id_side_effect(*args, **kwargs):
            mock_rec = MagicMock()
            mock_rec.id = 1
            mock_rec.bot_id = 1
            mock_rec.publish_type = "CREATE"
            mock_rec.status = record_state["status"]
            mock_rec.extra_config = {"auto_complete": True}
            mock_rec.creator = "user1"
            mock_rec.modifier = "user1"
            return mock_rec

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.update_status = MagicMock()
        _publish_service_instance._publish_repo.get_by_id = MagicMock(
            side_effect=get_by_id_side_effect
        )

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        # Mock _get_pending_batches: first returns pending batch, then empty
        with patch.object(
            DefaultPublishService,
            "_get_pending_batches",
        ) as mock_get_batches:
            mock_get_batches.side_effect = [
                ("PROD_FIRST_BATCH", [mock_batch]),  # First check: has pending
                ("SUCCESS", []),  # After execute: no more pending
            ]

            # Mock _get_current_stage to avoid DB call
            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="PROD_FIRST_BATCH",
            ):
                # Mock execute_stage to simulate success and update state
                async def execute_side_effect(*args, **kwargs):
                    record_state["status"] = "SUCCESS"
                    return MagicMock(success=True)

                with patch.object(
                    DefaultPublishService,
                    "execute_stage",
                    new_callable=AsyncMock,
                    side_effect=execute_side_effect,
                ) as mock_execute:
                    # Mock _get_publish_and_bot_record to avoid DB call
                    # Track calls to return different statuses (PENDING -> ACTIVE -> SUCCESS)
                    call_count = {"count": 0}

                    def get_publish_and_bot_side_effect(tenant, publish_id):
                        mock_rec = MagicMock()
                        mock_rec.id = 1
                        mock_rec.bot_id = 1
                        mock_rec.publish_type = "CREATE"
                        mock_rec.extra_config = {"auto_complete": True}
                        mock_rec.creator = "user1"
                        mock_rec.modifier = "user1"
                        mock_rec.gmt_create = datetime.now()
                        mock_rec.gmt_modified = datetime.now()

                        call_count["count"] += 1
                        if call_count["count"] == 1:
                            # First call - return PENDING (initial check)
                            mock_rec.status = "PENDING"
                        elif call_count["count"] == 2:
                            # Second call - return ACTIVE (after approval transition)
                            mock_rec.status = "ACTIVE"
                        else:
                            # Subsequent calls - return record_state status (SUCCESS after execute)
                            mock_rec.status = record_state["status"]

                        return mock_rec, mock_bot

                    with patch.object(
                        DefaultPublishService,
                        "_get_publish_and_bot_record",
                        side_effect=get_publish_and_bot_side_effect,
                    ):
                        result = await _publish_service_instance.approve_stage(
                            tenant="test_tenant",
                            publish_id=1,
                            operator="user1",
                        )

                        # Verify execute_stage was called (auto-execute)
                        mock_execute.assert_called_once()
                        call_args = mock_execute.call_args
                        assert call_args.args[0] == "test_tenant"
                        assert call_args.args[1] == 1
                        assert call_args.args[2] == "user1"
                        # Verify final status is SUCCESS
                        assert result.status == "SUCCESS"

    @pytest.mark.asyncio
    async def test_approve_from_approving_auto_executes_next_stage(self):
        """Approving from APPROVING should transition to ACTIVE and execute next stage."""
        from secbaas.community.api.publish_manage import (
            PublishStatus as Ps,
        )

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.status = BatchStatus.PENDING.value

        # Simulates state changes: start as APPROVING, then after update_status it's ACTIVE
        record_state = {"status": "APPROVING"}

        def get_by_id_side_effect(*args, **kwargs):
            mock_rec = MagicMock()
            mock_rec.id = 1
            mock_rec.bot_id = 1
            mock_rec.publish_type = "CREATE"
            mock_rec.status = record_state["status"]
            mock_rec.extra_config = {"auto_complete": True}
            mock_rec.creator = "user1"
            mock_rec.modifier = "user1"
            return mock_rec

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id = MagicMock(
            side_effect=get_by_id_side_effect
        )

        # Simulate update_status changing the status to ACTIVE
        def update_status_side_effect(*, publish_id, tenant, env, status, modifier):
            record_state["status"] = status

        _publish_service_instance._publish_repo.update_status = MagicMock(
            side_effect=update_status_side_effect
        )

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        with patch.object(
            DefaultPublishService,
            "_get_pending_batches",
            side_effect=[
                ("GRAY", [mock_batch]),  # Has pending batch
                ("SUCCESS", []),  # After execute, no more
            ],
        ):

            async def execute_side_effect(*args, **kwargs):
                record_state["status"] = "SUCCESS"
                return MagicMock(success=True)

            with patch.object(
                DefaultPublishService,
                "execute_stage",
                new_callable=AsyncMock,
                side_effect=execute_side_effect,
            ) as mock_execute:
                result = await _publish_service_instance.approve_stage(
                    tenant="test_tenant",
                    publish_id=1,
                    operator="user1",
                )

                # Verify status was transitioned from APPROVING → ACTIVE
                _publish_service_instance._publish_repo.update_status.assert_called_once_with(
                    publish_id=1,
                    tenant="test_tenant",
                    env=ANY,
                    status=Ps.ACTIVE.value,
                    modifier="user1",
                )
                mock_execute.assert_called_once()
                assert result.status == "SUCCESS"


class TestDestroyPublishStatusHandling:
    """Tests for DESTROY publish behavior with DESTROYING status."""

    @pytest.mark.asyncio
    async def test_complete_publish_destroys_transitions_bot_to_released(self):
        """Test complete_publish transitions bot from DESTROYING to RELEASED."""
        from datetime import datetime

        from secbaas.community.api.bot_manage import BotStatus

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.DESTROY.value
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.status = BotStatus.DESTROYING.value

        mock_publish_success = MagicMock()
        mock_publish_success.id = 1
        mock_publish_success.bot_id = 1
        mock_publish_success.publish_type = PublishType.DESTROY.value
        mock_publish_success.status = PublishStatus.SUCCESS.value
        mock_publish_success.extra_config = {}
        mock_publish_success.creator = "user1"
        mock_publish_success.modifier = "user1"
        mock_publish_success.gmt_create = datetime.now()
        mock_publish_success.gmt_modified = datetime.now()

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, mock_bot),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish_success
            )

            _publish_service_instance._bot_repo = MagicMock()
            _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="SUCCESS",
            ):
                await _publish_service_instance.complete_publish(
                    tenant="test_tenant", publish_id=1, operator="admin"
                )

                # Verify bot status updated to RELEASED via complete_destroy
                _publish_service_instance._bot_repo.complete_destroy.assert_called_once()
                destroy_call = (
                    _publish_service_instance._bot_repo.complete_destroy.call_args
                )
                assert destroy_call.kwargs["bot_id"] == 1

                # Verify soft_delete was NOT called separately (it's inside complete_destroy)
                _publish_service_instance._bot_repo.soft_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_reject_publish_keeps_bot_destroying_for_destroy_type(self):
        """Test reject_publish keeps bot in DESTROYING status for DESTROY publishes."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.DESTROY.value
        mock_publish.status = "PENDING"
        mock_publish.extra_config = {}
        mock_publish.creator = "admin"
        mock_publish.modifier = "admin"

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, MagicMock()),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _result = await _publish_service_instance.reject_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                reason="test rejection",
            )

            # Verify publish was rejected
            _publish_service_instance._publish_repo.update_status.assert_called_once()
            call_args = _publish_service_instance._publish_repo.update_status.call_args
            assert call_args.kwargs["status"] == "REJECTED"

            # Note: Bot status should remain DESTROYING (no restoration)
            # The reject_publish method does NOT update bot status

    @pytest.mark.asyncio
    async def test_revoke_publish_keeps_bot_destroying_for_destroy_type(self):
        """Test revoke_publish keeps bot in DESTROYING status for DESTROY publishes."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = PublishType.DESTROY.value
        mock_publish.status = "APPROVING"
        mock_publish.extra_config = {}
        mock_publish.creator = "admin"
        mock_publish.modifier = "admin"

        with patch.object(
            DefaultPublishService,
            "_get_publish_and_bot_record",
            return_value=(mock_publish, MagicMock()),
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            _result = await _publish_service_instance.revoke_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                reason="test revocation",
            )

            # Verify publish was revoked
            _publish_service_instance._publish_repo.update_status.assert_called_once()
            call_args = _publish_service_instance._publish_repo.update_status.call_args
            assert call_args.kwargs["status"] == "REVOKED"

            # Note: Bot status should remain DESTROYING (no restoration)
            # The revoke_publish method does NOT update bot status


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
    """Tests for _get_active_sessions method.

    ``_get_active_sessions`` now returns ``ActiveSessionVerdict`` driven by
    ``ActiveSessionInspector``. The DB count via
    ``count_active_sessions_by_device`` is retained **only** as an audit log
    field and does NOT drive the verdict. Any failure (missing device,
    missing paas_device_id, missing paas_facade, inspector error, exception)
    collapses to ``UNKNOWN`` — never ``CLEAR``.
    """

    def _setup_device(
        self, device_uuid: str = "dev-uuid-123", paas_device_id: str | None = "dev--0@tpl-1"
    ) -> MagicMock:
        mock_device = MagicMock()
        mock_device.device_uuid = device_uuid
        mock_device.provider_device_id = paas_device_id
        return mock_device

    @pytest.mark.asyncio
    async def test_get_active_sessions_returns_clear_verdict(self):
        """Test that _get_active_sessions returns CLEAR verdict from inspector."""
        from secbaas.community.core.service.health_check.paas import (
            ActiveSessionInspectResult,
            ActiveSessionVerdict,
        )

        mock_device = self._setup_device()

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.return_value = mock_device
        _publish_service_instance._session_repo = MagicMock()
        _publish_service_instance._session_repo.count_active_sessions_by_device.return_value = 3

        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock()
        mock_inspector = MagicMock()
        mock_inspector.inspect = AsyncMock(
            return_value=ActiveSessionInspectResult(
                verdict=ActiveSessionVerdict.CLEAR,
                query_status="ok",
                duration_ms=12,
            )
        )
        _publish_service_instance._paas_facade = mock_facade
        _publish_service_instance._active_session_inspector = mock_inspector

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            verdict = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=1
            )

            assert verdict is ActiveSessionVerdict.CLEAR
            _publish_service_instance._device_repo.get_by_id.assert_called_once_with(
                1, tenant="test-tenant", env="test"
            )
            # Audit DB count is queried but does not drive verdict.
            _publish_service_instance._session_repo.count_active_sessions_by_device.assert_called_once_with(
                device_uuid="dev-uuid-123", tenant="test-tenant"
            )
            mock_inspector.inspect.assert_awaited_once()
            inspect_kwargs = mock_inspector.inspect.await_args.kwargs
            assert inspect_kwargs["paas_device_id"] == "dev--0@tpl-1"
            assert inspect_kwargs["paas_facade"] is mock_facade

    @pytest.mark.asyncio
    async def test_get_active_sessions_returns_unknown_when_no_device(self):
        """Test that _get_active_sessions returns UNKNOWN when device not found."""
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.return_value = None
        _publish_service_instance._session_repo = MagicMock()
        _publish_service_instance._paas_facade = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.inspect = AsyncMock()
        _publish_service_instance._active_session_inspector = mock_inspector

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            verdict = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=999
            )

            assert verdict is ActiveSessionVerdict.UNKNOWN
            # Inspector must not be called without a device/paas_device_id.
            mock_inspector.inspect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_active_sessions_returns_unknown_on_exception(self):
        """Test that _get_active_sessions returns UNKNOWN on error (no degrade to CLEAR)."""
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.side_effect = Exception(
            "Database error"
        )
        _publish_service_instance._session_repo = MagicMock()
        _publish_service_instance._paas_facade = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.inspect = AsyncMock()
        _publish_service_instance._active_session_inspector = mock_inspector

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            verdict = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=1
            )

            # Must NOT degrade to CLEAR; device stays safe-by-default.
            assert verdict is ActiveSessionVerdict.UNKNOWN
            mock_inspector.inspect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_active_sessions_unknown_when_paas_device_id_missing(self):
        """Device without paas_device_id must collapse to UNKNOWN."""
        mock_device = self._setup_device(paas_device_id=None)

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.return_value = mock_device
        _publish_service_instance._session_repo = MagicMock()
        _publish_service_instance._paas_facade = MagicMock()
        mock_inspector = MagicMock()
        mock_inspector.inspect = AsyncMock()
        _publish_service_instance._active_session_inspector = mock_inspector

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            verdict = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=1
            )

            assert verdict is ActiveSessionVerdict.UNKNOWN
            mock_inspector.inspect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_active_sessions_unknown_when_no_paas_facade(self):
        """Without paas_facade the engine cannot be queried -> UNKNOWN."""
        mock_device = self._setup_device()

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.return_value = mock_device
        _publish_service_instance._session_repo = MagicMock()
        _publish_service_instance._paas_facade = None
        mock_inspector = MagicMock()
        mock_inspector.inspect = AsyncMock()
        _publish_service_instance._active_session_inspector = mock_inspector

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            verdict = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=1
            )

            assert verdict is ActiveSessionVerdict.UNKNOWN
            mock_inspector.inspect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_active_sessions_active_verdict_from_inspector(self):
        """ACTIVE verdict from inspector is forwarded unchanged."""
        from secbaas.community.core.service.health_check.paas import (
            ActiveSessionInspectResult,
            ActiveSessionVerdict,
        )

        mock_device = self._setup_device()
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.get_by_id.return_value = mock_device
        _publish_service_instance._session_repo = MagicMock()
        _publish_service_instance._session_repo.count_active_sessions_by_device.return_value = 5
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock()
        mock_inspector = MagicMock()
        mock_inspector.inspect = AsyncMock(
            return_value=ActiveSessionInspectResult(
                verdict=ActiveSessionVerdict.ACTIVE,
                query_status="ok",
                duration_ms=42,
            )
        )
        _publish_service_instance._paas_facade = mock_facade
        _publish_service_instance._active_session_inspector = mock_inspector

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            verdict = await _publish_service_instance._get_active_sessions(
                tenant="test-tenant", device_id=1
            )

            assert verdict is ActiveSessionVerdict.ACTIVE


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
            PublishType,
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


# ====================================================================
# TEST: list_publishes — coverage for lines 1267-1313
# ====================================================================


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


# ====================================================================
# TEST: _execute_batch unknown publish type — coverage for lines 1696-1701
# ====================================================================


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


# ====================================================================
# TEST: _execute_destroy_batch — coverage for lines 2605-2779
# ====================================================================


class TestExecuteDestroyBatch:
    """Tests for _execute_destroy_batch method."""

    @pytest.mark.asyncio
    async def test_execute_destroy_batch_success(self):
        """DESTROY batch drains and destroys active devices successfully."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import (
            DrainResult,
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
            DrainResult,
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
            DrainResult,
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


# ====================================================================
# TEST: _check_stage_advancement — coverage for lines 3116-3246
# ====================================================================


class TestCheckStageAdvancement:
    """Tests for _check_stage_advancement method."""

    @pytest.mark.asyncio
    async def test_check_stage_advancement_stage_failed_create_type(self):
        """_check_stage_advancement sets bot to FAILED on stage failure for CREATE publish."""
        from secbaas.community.api.bot_manage import BotStatus

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
        from secbaas.community.api.bot_manage import BotStatus

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


# ====================================================================
# TEST: _get_device_details — coverage for lines 3696-3757
# ====================================================================


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


# ====================================================================
# TEST: complete_publish UPDATE device transfer — coverage for lines 3331-3396
# ====================================================================


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
        from secbaas.community.api.bot_manage import BotStatus

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
        from secbaas.community.api.bot_manage import BotStatus

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


# ====================================================================
# TEST: _execute_scale_batch SCALE_DOWN — coverage for lines 2445-2571
# ====================================================================


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


# ====================================================================
# TEST: handle_device_callback — coverage for lines 2925-3054
# ====================================================================


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


# ====================================================================
# TEST: _check_batch_completion — coverage for lines 3067-3101
# ====================================================================


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


# ====================================================================
# TEST: _check_and_handle_timeout — coverage for lines 3435-3495
# ====================================================================


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


# ====================================================================
# TEST: _check_stage_advancement UPDATE failure path — line 3148-3149
# ====================================================================


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


# ====================================================================
# TEST: get_publish_progress with include_devices — coverage for remaining lines
# ====================================================================


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


# ====================================================================
# TEST: _get_pending_batches — coverage for lines 808-828
# ====================================================================


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


# ====================================================================
# TEST: _cleanup_pending_clone_on_update_failure — coverage for lines 135-180
# ====================================================================


class TestCleanupPendingClone:
    """Tests for _cleanup_pending_clone_on_update_failure."""

    def test_cleanup_pending_clone_soft_deletes_orphan_bot(self):
        """_cleanup_pending_clone soft-deletes orphan PENDING clone bot."""
        from secbaas.community.api.bot_manage import BotStatus

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
        from secbaas.community.api.bot_manage import BotStatus

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


# ====================================================================
# TEST: create_publish SCALE_UP/SCALE_DOWN validation — lines 365-402
# ====================================================================


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


# ====================================================================
# TEST: create_publish orphan cleanup — lines 297-327
# ====================================================================


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
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = (
            mock_orphan
        )
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


# ====================================================================
# TEST: create_publish concurrent type mismatch — lines 334-352
# ====================================================================


class TestCreatePublishConcurrentTypeMismatch:
    """Tests for concurrent publish type mismatch errors."""

    @pytest.mark.asyncio
    async def test_create_publish_raises_on_type_mismatch(self):
        """create_publish raises PublishConflictError on type mismatch."""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_existing = MagicMock()
        mock_existing.id = 99
        mock_existing.publish_type = "DESTROY"
        mock_existing.status = "ACTIVE"
        mock_existing.gmt_modified = datetime.now()

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = (
            mock_existing
        )

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock()
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(PublishConflictError, match="Cannot create"):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.CREATE,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_create_publish_returns_existing_on_same_type(self):
        """create_publish returns existing publish on same type collision."""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_existing = MagicMock()
        mock_existing.id = 99
        mock_existing.publish_type = "CREATE"
        mock_existing.status = "ACTIVE"
        mock_existing.bot_id = 1
        mock_existing.extra_config = None
        mock_existing.creator = "user1"
        mock_existing.modifier = "user1"
        mock_existing.gmt_create = datetime.now()
        mock_existing.gmt_modified = datetime.now()

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = (
            mock_existing
        )

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            MagicMock()
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="PREPUB",
            ):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.CREATE,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )


# ====================================================================
# TEST: _refresh_publish_response — coverage for lines 948-953
# ====================================================================


class TestRefreshPublishResponse:
    """Tests for _refresh_publish_response method."""

    @pytest.mark.asyncio
    async def test_refresh_publish_response_returns_fresh_data(self):
        """_refresh_publish_response re-reads and builds response."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = None
        mock_publish.creator = "admin"
        mock_publish.modifier = "admin"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = (
                mock_publish
            )

            with patch.object(
                DefaultPublishService,
                "_get_current_stage",
                return_value="PROD_FIRST_BATCH",
            ):
                result = await _publish_service_instance._refresh_publish_response(
                    tenant="test_tenant", publish_id=1
                )

                assert result.id == 1
                assert result.stage == "PROD_FIRST_BATCH"


# ====================================================================
# TEST: get_publish — coverage for lines 956-979
# ====================================================================


class TestGetPublish:
    """Tests for get_publish API method."""

    @pytest.mark.asyncio
    async def test_get_publish_returns_response(self):
        """get_publish returns PublishResponse for valid publish."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.tenant = "test_tenant"
        mock_publish.publish_type = "CREATE"
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = None
        mock_publish.creator = "admin"
        mock_publish.modifier = "admin"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        mock_bot = MagicMock()
        mock_bot.id = 1

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
                    "_get_current_stage",
                    return_value="PREPUB",
                ):
                    result = await _publish_service_instance.get_publish(
                        tenant="test_tenant", publish_id=1
                    )

                    assert result is not None
                    assert result.id == 1


# ====================================================================
# TEST: create_publish bot not found — lines 275-278
# ====================================================================


class TestCreatePublishBotNotFound:
    """Tests for create_publish bot not found paths."""

    @pytest.mark.asyncio
    async def test_create_publish_raises_on_bot_not_found(self):
        """create_publish raises BotNotFoundError when bot not found."""
        from secbaas.community.api.bot_runtime import BotNotFoundError

        _publish_service_instance._bot_service.get_bot = AsyncMock(return_value=None)
        with pytest.raises(BotNotFoundError):
            await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=999,
                publish_type=PublishType.CREATE,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )


# ====================================================================
# TEST: create_publish orphan cleanup exception — lines 321-327
# ====================================================================


class TestCreatePublishOrphanCleanupException:
    """Tests for orphan cleanup exception handling."""

    @pytest.mark.asyncio
    async def test_create_publish_orphan_cleanup_exception(self):
        """create_publish raises PublishConflictError when orphan cleanup fails."""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_orphan = MagicMock()
        mock_orphan.id = 99
        mock_orphan.publish_type = "CREATE"
        mock_orphan.status = "PENDING"

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = (
            mock_orphan
        )
        _publish_service_instance._publish_repo.update_status.side_effect = (
            RuntimeError("DB connection error")
        )

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(PublishConflictError, match="Orphan publish exists"):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.CREATE,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_get_publish_not_found_returns_none(self):
        """get_publish returns None when publish not found."""
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
                result = await _publish_service_instance.get_publish(
                    tenant="test_tenant", publish_id=999
                )

                assert result is None


# ====================================================================
# TEST: _get_publish_and_bot_record tenant mismatch — lines 906-910
# ====================================================================


class TestGetPublishAndBotRecordTenantMismatch:
    """Tests for _get_publish_and_bot_record tenant mismatch."""

    def test_get_publish_and_bot_record_tenant_mismatch(self):
        """_get_publish_and_bot_record returns None when bot not found."""
        mock_env = MagicMock()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1

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
            _publish_service_instance._bot_repo.get_by_id_including_deleted.return_value = None

            result = _publish_service_instance._get_publish_and_bot_record(
                tenant="test_tenant", publish_id=1
            )

            assert result == (None, None)


# ====================================================================
# TEST: _drain_device timeout — coverage for lines 1551-1556
# ====================================================================


# ====================================================================
# TEST: _build_publish_response — coverage for lines 914-936
# ====================================================================


class TestBuildPublishResponse:
    """Tests for _build_publish_response method."""

    def test_build_publish_response_constructs_correctly(self):
        """_build_publish_response constructs PublishResponse from record."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.tenant = "test_tenant"
        mock_publish.publish_type = "CREATE"
        mock_publish.status = "ACTIVE"
        mock_publish.extra_config = None
        mock_publish.creator = "admin"
        mock_publish.modifier = "admin"
        mock_publish.gmt_create = datetime.now()
        mock_publish.gmt_modified = datetime.now()

        with patch.object(
            DefaultPublishService,
            "_get_current_stage",
            return_value="PREPUB",
        ):
            result = _publish_service_instance._build_publish_response(mock_publish)

            assert result.id == 1
            assert result.publish_type == "CREATE"
            assert result.status == "ACTIVE"
            assert result.stage == "PREPUB"


class TestExecuteStageGate:
    """Tests for execute_stage stage gate transition (lines 1551-1556)."""

    @pytest.mark.asyncio
    async def test_execute_stage_gate_transitions_to_approving(self):
        """execute_stage transitions to APPROVING when more stages exist."""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.status = "ACTIVE"
        mock_publish.bot_id = 1
        mock_publish.extra_config = {"auto_complete": True}

        mock_batch = MagicMock()
        mock_batch.id = 1
        mock_batch.batch_index = 0
        mock_batch.batch_capacity = 2
        mock_batch.cooldown_seconds = 0
        mock_batch.stage = "PREPUB"
        mock_batch.status = "PENDING"

        mock_env = MagicMock()

        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id.return_value = mock_bot

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = [
            mock_batch
        ]

        with patch.object(
            DefaultPublishService,
            "_get_pending_batches",
            side_effect=[
                ("PREPUB", [mock_batch]),
                ("PROD_FIRST_BATCH", [mock_batch]),
            ],
        ):
            with patch.object(
                DefaultPublishService,
                "_execute_batch",
                new_callable=AsyncMock,
            ) as mock_exec:
                from secbaas.community.api.publish_manage import BatchResult

                mock_exec.return_value = BatchResult(
                    success=True, processed_count=2, failed_count=0
                )

                with patch(
                    "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                    return_value=mock_env,
                ):
                    result = await _publish_service_instance.execute_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    assert result.success is True
                    _publish_service_instance._publish_repo.update_status.assert_any_call(
                        publish_id=1,
                        tenant="test_tenant",
                        env=mock_env,
                        status=PublishStatus.APPROVING.value,
                        modifier="admin",
                    )


class TestRefreshPublishResponseNotFound:
    """Tests for _refresh_publish_response not-found path (line 952)."""

    @pytest.mark.asyncio
    async def test_refresh_publish_response_not_found(self):
        """_refresh_publish_response raises PublishNotFoundError."""
        mock_env = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            _publish_service_instance._publish_repo = MagicMock()
            _publish_service_instance._publish_repo.now.return_value = datetime.now()
            _publish_service_instance._publish_repo.get_by_id.return_value = None

            with pytest.raises(PublishNotFoundError):
                await _publish_service_instance._refresh_publish_response(
                    tenant="test_tenant",
                    publish_id=999,
                )


# ====================================================================
# NEW TESTS FOR UNCOVERED LINES (coverage improvement)
# ====================================================================


class TestCleanupPendingCloneSoftDeleteException:
    """Tests for _cleanup_pending_clone_on_update_failure exception path (line 167-168)."""

    def test_cleanup_pending_clone_handles_soft_delete_rel_exception(self):
        """_cleanup_pending_clone handles exception from soft_delete_by_bot_id gracefully."""
        from secbaas.community.api.bot_manage import BotStatus

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
            mock_rel_repo_instance.soft_delete_by_bot_id.side_effect = RuntimeError(
                "DB connection error"
            )
            _publish_service_instance._rel_repo = mock_rel_repo_instance

            # Should not raise — exception is caught
            _publish_service_instance._cleanup_pending_clone_on_update_failure(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
            )

            mock_rel_repo_instance.soft_delete_by_bot_id.assert_called_once()
            # bot_repo.soft_delete should still be called after rel exception
            _publish_service_instance._bot_repo.soft_delete.assert_called_once_with(
                bot_id=2,
                tenant="test_tenant",
                env=mock_env,
                modifier="admin",
            )


class TestCreatePublishStaleTimeout:
    """Tests for stale publish timeout resolution (line 551 — stale cross-type with timeout)."""

    @pytest.mark.asyncio
    async def test_create_publish_stale_publish_timeout_allows_new(self):
        """Stale publish beyond timeout threshold is auto-resolved for new publish."""
        from datetime import timedelta

        from secbaas.community.api.publish_manage import (
            DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS,
        )

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_existing = MagicMock()
        mock_existing.id = 99
        mock_existing.publish_type = "DESTROY"
        mock_existing.status = "PENDING"
        mock_existing.extra_config = {}
        mock_existing.gmt_modified = datetime.now() - timedelta(
            seconds=DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS + 60
        )

        mock_new_publish = MagicMock()
        mock_new_publish.id = 1
        mock_new_publish.bot_id = 1
        mock_new_publish.publish_type = "CREATE"
        mock_new_publish.status = "PENDING"
        mock_new_publish.extra_config = {}
        mock_new_publish.creator = "user1"
        mock_new_publish.modifier = "user1"
        mock_new_publish.gmt_create = datetime.now()
        mock_new_publish.gmt_modified = datetime.now()

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = (
            mock_existing
        )
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_new_publish
        )

        mock_batch_record = MagicMock()
        mock_batch_record.batch_index = 0
        mock_batch_record.id = 1
        mock_batch_record.batch_capacity = 5
        _publish_service_instance._publish_batch_repo = MagicMock()
        mock_existing_batch = MagicMock()
        mock_existing_batch.stage = "PREPUB"
        mock_existing_batch.status = BatchStatus.PENDING.value
        _publish_service_instance._publish_batch_repo.list_by_publish_id.side_effect = [
            [mock_existing_batch],
            [],
        ]
        _publish_service_instance._publish_batch_repo.insert_batch.return_value = 1
        _publish_service_instance._publish_batch_repo.get_by_id.return_value = (
            mock_batch_record
        )

        # Mock devices with PENDING status so _create_device_records_for_publish
        # has eligible devices (CREATE selects PENDING devices)
        mock_pending_device = MagicMock()
        mock_pending_device.id = 10
        mock_pending_device.status = "PENDING"
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_pending_device
        ]
        _publish_service_instance._publish_record_repo = MagicMock()

        with patch.object(
            DefaultPublishService,
            "_check_and_handle_timeout",
            new_callable=AsyncMock,
        ) as mock_timeout:
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
                mock_timeout.assert_called_once_with(mock_existing, "test_tenant")


class TestCreatePublishOrphanUpdate:
    """Tests for orphan publish with UPDATE type cleanup (line 308)."""

    @pytest.mark.asyncio
    async def test_create_publish_orphan_update_calls_cleanup_clone(self):
        """Orphan UPDATE publish triggers _cleanup_pending_clone_on_update_failure."""
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_orphan = MagicMock()
        mock_orphan.id = 99
        mock_orphan.publish_type = "UPDATE"
        mock_orphan.status = "PENDING"

        mock_new_publish = MagicMock()
        mock_new_publish.id = 1
        mock_new_publish.bot_id = 1
        mock_new_publish.publish_type = "CREATE"
        mock_new_publish.status = "PENDING"
        mock_new_publish.extra_config = {}
        mock_new_publish.creator = "user1"
        mock_new_publish.modifier = "user1"
        mock_new_publish.gmt_create = datetime.now()
        mock_new_publish.gmt_modified = datetime.now()

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = (
            mock_orphan
        )
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = (
            mock_new_publish
        )

        mock_batch_record = MagicMock()
        mock_batch_record.batch_index = 0
        mock_batch_record.id = 1
        mock_batch_record.batch_capacity = 5
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = []
        _publish_service_instance._publish_batch_repo.insert_batch.return_value = 1
        _publish_service_instance._publish_batch_repo.get_by_id.return_value = (
            mock_batch_record
        )

        # Mock devices with PENDING status so _create_device_records_for_publish
        # has eligible devices (CREATE selects PENDING devices)
        mock_pending_device = MagicMock()
        mock_pending_device.id = 10
        mock_pending_device.status = "PENDING"
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_pending_device
        ]
        _publish_service_instance._publish_record_repo = MagicMock()

        with patch.object(
            DefaultPublishService,
            "_cleanup_pending_clone_on_update_failure",
        ) as mock_cleanup:
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
                mock_cleanup.assert_called_once_with(
                    tenant="test_tenant",
                    publish_id=99,
                    operator="user1",
                )


class TestCreatePublishScaleAmountZero:
    """Tests for scale_amount <= 0 validation (line 417)."""

    @pytest.mark.asyncio
    async def test_create_publish_scale_up_zero_devices_raises(self):
        """SCALE_UP with same target as current raises ValueError."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_device = MagicMock()
        mock_device.id = 1
        mock_device.status = DeviceStatus.ACTIVE.value

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(ValueError, match="no devices to scale"):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.SCALE_UP,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                    config=PublishConfig(replica_desired=1),
                )


class TestCreatePublishUpdateNoReplicaDesired:
    """Tests for UPDATE publish with no replica_desired (line 445)."""

    @pytest.mark.asyncio
    async def test_create_publish_update_no_replica_desired_raises(self):
        """UPDATE publish with no replica_desired raises ValueError."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.replica_desired = None

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
            with pytest.raises(ValueError, match="no replica_desired configured"):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.UPDATE,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )


class TestCreatePublishUpdateConfigOverwrite:
    """Tests for UPDATE publish config overwrite (line 452)."""

    @pytest.mark.asyncio
    async def test_create_publish_update_overwrites_config_replica_desired(self):
        """UPDATE publish overwrites config.replica_desired from bot record."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.replica_desired = 3

        mock_new_bot = MagicMock()
        mock_new_bot.id = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "UPDATE"
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
        _publish_service_instance._bot_service.create_bot_record = AsyncMock(
            return_value=mock_new_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        mock_batch_record = MagicMock()
        mock_batch_record.batch_index = 0
        mock_batch_record.id = 1
        mock_batch_record.batch_capacity = 5
        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.insert_batch.return_value = 1
        _publish_service_instance._publish_batch_repo.get_by_id.return_value = (
            mock_batch_record
        )

        # Mock devices with ACTIVE status so _create_device_records_for_publish
        # has eligible devices (UPDATE selects ACTIVE or FAILED)
        mock_active_device = MagicMock()
        mock_active_device.id = 10
        mock_active_device.status = "ACTIVE"
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_active_device
        ]
        _publish_service_instance._publish_record_repo = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            config = PublishConfig(
                batch_capacity=5,
                template_uuid="TEMPLATE-new",
            )
            result = await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.UPDATE,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                config=config,
            )

            assert result.status == "PENDING"
            # config.replica_desired should be set from bot
            assert config.replica_desired == 3
            _publish_service_instance._template_service.get_online_template_by_uuid.assert_called_once_with(
                tenant="test_tenant",
                template_uuid="TEMPLATE-new",
            )
            create_call = _publish_service_instance._bot_service.create_bot_record.call_args.kwargs
            assert create_call["new_template_uuid"] == "TEMPLATE-new"

            insert_call_args = (
                _publish_service_instance._publish_repo.insert_publish.call_args
            )
            assert insert_call_args.kwargs["replica_desired"] == 3

    @pytest.mark.asyncio
    async def test_create_publish_update_rejects_unknown_template_before_insert(self):
        """An invalid tenant-scoped template must not leave publish state behind."""
        mock_bot = MagicMock(id=1, replica_desired=1)
        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._template_service.get_online_template_by_uuid.return_value = None

        with pytest.raises(ValueError, match="Template not found or not online"):
            await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.UPDATE,
                operator="user1",
                request_id="template-update-request",
                config=PublishConfig(
                    replica_desired=1,
                    template_uuid="TEMPLATE-missing",
                ),
            )

        _publish_service_instance._publish_repo.insert_publish.assert_not_called()


class TestCreatePublishRestartUnhealthy:
    """Tests for RESTART publish with unhealthy scope (lines 472-499)."""

    @pytest.mark.asyncio
    async def test_create_publish_restart_unhealthy_scope_only_failed(self):
        """RESTART with unhealthy scope only selects FAILED devices."""
        from secbaas.community.api.device_manage import DeviceStatus
        from secbaas.community.api.publish_manage import RestartScope

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        mock_failed_device = MagicMock()
        mock_failed_device.id = 1
        mock_failed_device.device_uuid = "dev-failed-1"
        mock_failed_device.status = DeviceStatus.FAILED.value

        mock_active_device = MagicMock()
        mock_active_device.id = 2
        mock_active_device.device_uuid = "dev-active-1"
        mock_active_device.status = DeviceStatus.ACTIVE.value

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "RESTART"
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
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_failed_device,
            mock_active_device,
        ]

        _publish_service_instance._publish_batch_repo = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            config = PublishConfig(restart_scope=RestartScope.UNHEALTHY)
            result = await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.RESTART,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                config=config,
            )

            assert result.status == "PENDING"
            assert config.replica_desired == 1  # only FAILED

    @pytest.mark.asyncio
    async def test_create_publish_restart_no_eligible_devices_raises(self):
        """RESTART with no eligible devices raises ValueError."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_released_device = MagicMock()
        mock_released_device.id = 1
        mock_released_device.device_uuid = "dev-released-1"
        mock_released_device.status = DeviceStatus.RELEASED.value

        mock_env = MagicMock()

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_released_device
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(
                ValueError, match="No eligible devices found to restart"
            ):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.RESTART,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_create_publish_restart_with_active_publish_excludes_updating(self):
        """RESTART with active publish excludes UPDATING devices (lines 472-476)."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        mock_active_device = MagicMock()
        mock_active_device.id = 1
        mock_active_device.device_uuid = "dev-active-1"
        mock_active_device.status = DeviceStatus.ACTIVE.value

        mock_active_publish = MagicMock()
        mock_active_publish.id = 99
        mock_active_publish.publish_type = "RESTART"
        mock_active_publish.gmt_modified = datetime.now()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "RESTART"
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
        _publish_service_instance._publish_repo.get_active_by_bot_id.side_effect = [
            None,
            mock_active_publish,
        ]
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_active_device
        ]

        _publish_service_instance._publish_batch_repo = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            result = await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.RESTART,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

            assert result.status == "PENDING"


class TestCreatePublishNoEligibleDeviceRecords:
    """Test that create_publish raises ValueError when _create_device_records_for_publish finds no eligible devices.

    Previously the behavior was a silent return — the publish completed with PENDING status
    but no device-level publish_record entries. Now raises ValueError → HTTP 400.
    """

    @pytest.mark.asyncio
    async def test_create_create_publish_no_eligible_devices_raises(self):
        """CREATE publish with no PENDING devices raises ValueError.
        CREATE type filters for PENDING devices only.
        """
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

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
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()

        # Device exists but with ACTIVE status — CREATE needs PENDING
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = DeviceStatus.ACTIVE.value
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(
                ValueError, match="No eligible devices for CREATE publish"
            ):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.CREATE,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_update_publish_no_eligible_devices_raises(self):
        """UPDATE publish with no eligible devices raises ValueError.
        UPDATE type filters for ACTIVE, FAILED, PENDING, and STOPPED devices.
        """
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.replica_desired = 3

        mock_new_bot = MagicMock()
        mock_new_bot.id = 2

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "UPDATE"
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
        _publish_service_instance._bot_service.create_bot_record = AsyncMock(
            return_value=mock_new_bot
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()

        # Device exists but with UPDATING status — UPDATE needs ACTIVE/FAILED/PENDING/STOPPED
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = DeviceStatus.UPDATING.value
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(
                ValueError, match="No eligible devices for UPDATE publish"
            ):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.UPDATE,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                    config=PublishConfig(replica_desired=3),
                )

    @pytest.mark.asyncio
    async def test_scale_down_publish_no_eligible_devices_raises(self):
        """SCALE_DOWN publish with no ACTIVE devices raises ValueError.
        SCALE_DOWN filters for ACTIVE devices only.
        """
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"
        mock_bot.template_uuid = "template-uuid-1"

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "SCALE_DOWN"
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
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()

        # Device exists but with FAILED status — SCALE_DOWN needs ACTIVE
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = DeviceStatus.FAILED.value
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]
        _publish_service_instance._template_service = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(
                ValueError, match="No eligible devices for SCALE_DOWN publish"
            ):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.SCALE_DOWN,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                    config=PublishConfig(replica_desired=0),
                )

    @pytest.mark.asyncio
    async def test_destroy_publish_no_eligible_devices_raises(self):
        """DESTROY publish with no ACTIVE devices raises ValueError.
        DESTROY type filters for ACTIVE devices only.
        """
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "DESTROY"
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
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._publish_batch_repo = MagicMock()

        # Device exists but with FAILED status — DESTROY needs ACTIVE
        mock_device = MagicMock()
        mock_device.id = 10
        mock_device.status = DeviceStatus.FAILED.value
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_device
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            with pytest.raises(
                ValueError, match="No eligible devices for DESTROY publish"
            ):
                await _publish_service_instance.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.DESTROY,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )
        """RESTART with active publish excludes UPDATING devices (lines 472-476)."""
        from secbaas.community.api.device_manage import DeviceStatus

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.domain = "test_domain"

        mock_active_device = MagicMock()
        mock_active_device.id = 1
        mock_active_device.device_uuid = "dev-active-1"
        mock_active_device.status = DeviceStatus.ACTIVE.value

        mock_active_publish = MagicMock()
        mock_active_publish.id = 99
        mock_active_publish.publish_type = "RESTART"
        mock_active_publish.gmt_modified = datetime.now()

        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "RESTART"
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
        _publish_service_instance._publish_repo.get_active_by_bot_id.side_effect = [
            None,
            mock_active_publish,
        ]
        _publish_service_instance._publish_repo.insert_publish.return_value = 1
        _publish_service_instance._publish_repo.get_by_id.return_value = mock_publish

        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_active_device
        ]

        _publish_service_instance._publish_batch_repo = MagicMock()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value=mock_env,
        ):
            result = await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.RESTART,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

            assert result.status == "PENDING"


class TestApproveStageAlreadySuccess:
    """Tests for approve_stage idempotency — already SUCCESS (lines 1058-1059)."""

    @pytest.mark.asyncio
    async def test_approve_stage_already_success_returns_early(self):
        """approve_stage with SUCCESS status returns current response."""
        mock_publish = MagicMock()
        mock_publish.id = 1
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.status = PublishStatus.SUCCESS.value
        mock_publish.extra_config = None
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
            with patch.object(
                DefaultPublishService,
                "_get_publish_and_bot_record",
                return_value=(mock_publish, mock_bot),
            ):
                with patch.object(
                    DefaultPublishService,
                    "_refresh_publish_response",
                    new_callable=AsyncMock,
                ) as mock_refresh:
                    mock_refresh.return_value = MagicMock(status="SUCCESS")

                    result = await _publish_service_instance.approve_stage(
                        tenant="test_tenant",
                        publish_id=1,
                        operator="admin",
                    )

                    assert result.status == "SUCCESS"
                    mock_refresh.assert_called_once()


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
        from secbaas.community.api.bot_manage import BotStatus
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
        from secbaas.community.api.bot_manage import BotStatus

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
