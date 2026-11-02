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
        _publish_service_instance._publish_repo.list_active_by_bot_ids.return_value = [
            mock_orphan
        ]
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
        _publish_service_instance._bot_repo = MagicMock()
        _clone_pending_bot.return_value = mock_new_bot.id
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
            create_call = _clone_pending_bot.call_args.kwargs
            assert create_call["template_uuid"] == "TEMPLATE-new"

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
        _publish_service_instance._bot_repo = MagicMock()
        _clone_pending_bot.return_value = mock_new_bot.id
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
