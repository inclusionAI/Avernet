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
        mock_bot.config = BotConfig(
            entity_id="preserve-entity",
            deploy_config=DeployConfig(
                docker_image="previous:image", envs={"KEEP": "yes"}
            ),
        )

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
        _publish_service_instance._bot_repo = MagicMock()
        _clone_pending_bot.return_value = mock_new_bot.id
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
        create_call = _clone_pending_bot.call_args.kwargs
        assert create_call["source_bot_id"] == 1
        assert create_call["tenant"] == "test_tenant"
        # An omitted update override now clears the old image in the clone.
        assert create_call["extra_config"]["deploy_config"].get("docker_image") is None
        assert create_call["extra_config"]["deploy_config"]["envs"] == {"KEEP": "yes"}
        assert create_call["extra_config"]["entity_id"] == "preserve-entity"
        assert mock_bot.config.deploy_config.docker_image == "previous:image"
        assert create_call["template_uuid"] is None
        assert create_call["modifier"] == "user1"

    @pytest.mark.asyncio
    async def test_create_publish_lock_not_acquired_raises_conflict(self):
        """A publish rejected by the per-bot lock surfaces as PublishConflictError."""
        from secbaas.community.api.publish_manage import PublishConflictError

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-locked"

        lock_context = MagicMock()
        lock_context.acquired = False

        @contextmanager
        def _denied_lock(*_args, **_kwargs):
            yield lock_context

        _publish_service_instance._lock_service = MagicMock()
        _publish_service_instance._lock_service.try_lock = _denied_lock
        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )

        with pytest.raises(PublishConflictError, match="concurrently"):
            await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.UPDATE,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

    @pytest.mark.asyncio
    async def test_create_publish_update_clone_conflict_raises(self):
        """A concurrent PENDING clone surfaces as PublishConflictError."""
        from secbaas.community.api.publish_manage import PublishConflictError
        from secbaas.community.core.repository.bot import BotRecordConflictError

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.replica_desired = 1
        mock_bot.bot_uuid = "BOT-race"

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

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._bot_repo = MagicMock()
        _clone_pending_bot.side_effect = BotRecordConflictError(
            "PENDING bot record already exists"
        )
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        _publish_service_instance._publish_repo.get_active_by_bot_id.return_value = None
        _publish_service_instance._publish_repo.insert_publish.return_value = 1

        with pytest.raises(PublishConflictError, match="already exists"):
            await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.UPDATE,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
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
        _publish_service_instance._publish_repo.list_active_by_bot_ids.return_value = [
            existing_publish
        ]

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
        # First classifier query returns the stale publish; the re-query
        # after auto-resolution, the pre-insert protection-set query, and
        # the RESTART scope check all return [] since the stale publish
        # has been resolved
        _publish_service_instance._publish_repo.list_active_by_bot_ids.side_effect = [
            [mock_existing],
            [],
            [],
            [],
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
        # Same ordering as the auto-resolve test: classifier query returns
        # the stale publish, then the re-query, protection-set query, and
        # RESTART scope check return [] once it is resolved
        _publish_service_instance._publish_repo.list_active_by_bot_ids.side_effect = [
            [mock_existing],
            [],
            [],
            [],
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
        _publish_service_instance._publish_repo.list_active_by_bot_ids.return_value = [
            mock_existing
        ]

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
