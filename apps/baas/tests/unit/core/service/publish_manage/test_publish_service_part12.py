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
        _publish_service_instance._publish_repo.list_active_by_bot_ids.return_value = [
            mock_orphan
        ]
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


class TestCleanupPendingCloneSoftDeleteException:
    """Tests for _cleanup_pending_clone_on_update_failure exception path (line 167-168)."""

    def test_cleanup_pending_clone_handles_soft_delete_rel_exception(self):
        """_cleanup_pending_clone handles exception from soft_delete_by_bot_id gracefully."""

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
        _publish_service_instance._publish_repo.list_active_by_bot_ids.return_value = [
            mock_existing
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


class TestCreatePublishStaleUpdateCleanup:
    """Stale UPDATE publish in create_publish retires its clone before FAILED write."""

    @pytest.mark.asyncio
    async def test_create_publish_stale_update_calls_cleanup_clone(self):
        from datetime import timedelta

        from secbaas.community.api.publish_manage import (
            DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS,
        )

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_existing = MagicMock()
        mock_existing.id = 99
        mock_existing.publish_type = "UPDATE"
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
        # First classifier query returns the stale UPDATE publish; the
        # re-query after auto-resolution and the pre-insert protection-set
        # query return none left.
        _publish_service_instance._publish_repo.list_active_by_bot_ids.side_effect = [
            [mock_existing],
            [],
            [],
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

        mock_pending_device = MagicMock()
        mock_pending_device.id = 10
        mock_pending_device.status = "PENDING"
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_pending_device
        ]
        _publish_service_instance._publish_record_repo = MagicMock()

        with patch.object(
            DefaultPublishService, "_check_and_handle_timeout", new_callable=AsyncMock
        ):
            with patch.object(
                DefaultPublishService, "_cleanup_pending_clone_on_update_failure"
            ) as mock_cleanup:
                ordering = MagicMock()
                ordering.attach_mock(mock_cleanup, "cleanup_clone")
                ordering.attach_mock(
                    _publish_service_instance._publish_repo.update_status,
                    "update_status",
                )
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
            tenant="test_tenant", publish_id=99, operator="user1"
        )
        call_names = [name for name, _, _ in ordering.mock_calls]
        failed_writes = [
            i
            for i, (name, _, kwargs) in enumerate(ordering.mock_calls)
            if name == "update_status" and kwargs.get("status") == "FAILED"
        ]
        assert call_names.index("cleanup_clone") < failed_writes[0]


class TestClaimSetProtection:
    """Sweep protection set: published clone claims + device-owning generations."""

    def test_owns_live_device_binding_true_when_rel_is_live_binding(self):
        rel = SimpleNamespace(device_uuid="dev-1")
        binding = SimpleNamespace(bot_id=7)
        _publish_service_instance._rel_repo = MagicMock()
        _publish_service_instance._rel_repo.list_by_bot_id.return_value = [rel]
        _publish_service_instance._rel_repo.get_by_device_uuid.return_value = binding

        assert _publish_service_instance._owns_live_device_binding(7, "t", "e") is True

    def test_owns_live_device_binding_false_when_device_bound_elsewhere(self):
        rel = SimpleNamespace(device_uuid="dev-1")
        binding = SimpleNamespace(bot_id=8)
        _publish_service_instance._rel_repo = MagicMock()
        _publish_service_instance._rel_repo.list_by_bot_id.return_value = [rel]
        _publish_service_instance._rel_repo.get_by_device_uuid.return_value = binding

        assert _publish_service_instance._owns_live_device_binding(7, "t", "e") is False

    def test_owns_live_device_binding_false_when_binding_missing(self):
        rel = SimpleNamespace(device_uuid="dev-1")
        _publish_service_instance._rel_repo = MagicMock()
        _publish_service_instance._rel_repo.list_by_bot_id.return_value = [rel]
        _publish_service_instance._rel_repo.get_by_device_uuid.return_value = None

        assert _publish_service_instance._owns_live_device_binding(7, "t", "e") is False

    def test_owns_live_device_binding_false_without_rels(self):
        _publish_service_instance._rel_repo = MagicMock()
        _publish_service_instance._rel_repo.list_by_bot_id.return_value = []

        assert _publish_service_instance._owns_live_device_binding(7, "t", "e") is False

    @pytest.mark.asyncio
    async def test_claim_set_collects_pointer_and_device_owning_generations(self):
        mock_bot = MagicMock()
        mock_bot.id = 1

        claimer = MagicMock()
        claimer.id = 77
        claimer.extra_config = {"target_bot_id": 555}

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

        generation = SimpleNamespace(id=42)

        _publish_service_instance._bot_service.get_bot = AsyncMock(
            return_value=mock_bot
        )
        _publish_service_instance._bot_repo.list_by_bot_uuid.return_value = []
        _publish_service_instance._bot_repo.list_by_bot_uuid_including_deleted.return_value = [
            generation
        ]
        _publish_service_instance._publish_repo = MagicMock()
        _publish_service_instance._publish_repo.now.return_value = datetime.now()
        # Classifier query: none active; protection-set query: one claimer.
        _publish_service_instance._publish_repo.list_active_by_bot_ids.side_effect = [
            [],
            [claimer],
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

        mock_pending_device = MagicMock()
        mock_pending_device.id = 10
        mock_pending_device.status = "PENDING"
        _publish_service_instance._device_repo = MagicMock()
        _publish_service_instance._device_repo.list_by_bot_id.return_value = [
            mock_pending_device
        ]
        _publish_service_instance._publish_record_repo = MagicMock()

        _publish_service_instance._rel_repo = MagicMock()
        _publish_service_instance._rel_repo.list_by_bot_id.return_value = [
            SimpleNamespace(device_uuid="dev-42")
        ]
        _publish_service_instance._rel_repo.get_by_device_uuid.return_value = (
            SimpleNamespace(bot_id=42)
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="dev",
        ):
            await _publish_service_instance.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.CREATE,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

        sweep_kwargs = _publish_service_instance._bot_repo.reconcile_unclaimed_pending_bots.call_args.kwargs
        excluded = sweep_kwargs["exclude_bot_ids"]
        assert 555 in excluded  # target_bot_id claimed by a non-finished publish
        assert 42 in excluded  # generation owning its device's only live binding
        assert 1 in excluded  # the resolved source row
