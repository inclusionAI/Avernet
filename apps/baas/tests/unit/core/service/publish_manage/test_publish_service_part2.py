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
        _publish_service_instance._bot_repo = MagicMock()
        _clone_pending_bot.return_value = 2
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

    def _set_up_update_publish_for_terminal_action(
        self, publish_status: str
    ) -> MagicMock:
        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = publish_status
        mock_record.bot_id = 1
        mock_record.publish_type = "UPDATE"
        mock_record.extra_config = {}
        mock_record.creator = "user1"
        mock_record.modifier = "user1"
        mock_record.gmt_create = "2024-01-01T00:00:00"
        mock_record.gmt_modified = "2024-01-01T00:00:00"

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = mock_record
        _publish_service_instance._publish_repo = mock_repo

        _publish_service_instance._bot_repo = MagicMock()
        _publish_service_instance._bot_repo.get_by_id_including_deleted.return_value = (
            mock_bot
        )

        _publish_service_instance._publish_batch_repo = MagicMock()
        _publish_service_instance._publish_batch_repo.list_by_publish_id.return_value = []
        return mock_repo

    @pytest.mark.asyncio
    async def test_reject_update_publish_cleans_clone_before_rejected(self):
        """Rejecting a PENDING UPDATE publish retires its clone BEFORE the REJECTED write."""
        mock_repo = self._set_up_update_publish_for_terminal_action("PENDING")

        with patch.object(
            DefaultPublishService, "_cleanup_pending_clone_on_update_failure"
        ) as mock_cleanup:
            ordering = MagicMock()
            ordering.attach_mock(mock_cleanup, "cleanup_clone")
            ordering.attach_mock(mock_repo.update_status, "update_status")

            await _publish_service_instance.reject_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                reason="Configuration error",
            )

        mock_cleanup.assert_called_once_with(
            tenant="test_tenant", publish_id=1, operator="admin"
        )
        mock_repo.update_status.assert_called_once_with(
            publish_id=1,
            tenant="test_tenant",
            env="dev",
            status="REJECTED",
            modifier="admin",
        )
        call_names = [name for name, _, _ in ordering.mock_calls]
        assert call_names.index("cleanup_clone") < call_names.index("update_status")

    @pytest.mark.asyncio
    async def test_revoke_update_publish_cleans_clone_before_revoked(self):
        """Revoking an APPROVING UPDATE publish retires its clone BEFORE the REVOKED write."""
        mock_repo = self._set_up_update_publish_for_terminal_action("APPROVING")

        with patch.object(
            DefaultPublishService, "_cleanup_pending_clone_on_update_failure"
        ) as mock_cleanup:
            ordering = MagicMock()
            ordering.attach_mock(mock_cleanup, "cleanup_clone")
            ordering.attach_mock(mock_repo.update_status, "update_status")

            await _publish_service_instance.revoke_publish(
                tenant="test_tenant",
                publish_id=1,
                operator="admin",
                reason="Change of plans",
            )

        mock_cleanup.assert_called_once_with(
            tenant="test_tenant", publish_id=1, operator="admin"
        )
        mock_repo.update_status.assert_called_once_with(
            publish_id=1,
            tenant="test_tenant",
            env="dev",
            status="REVOKED",
            modifier="admin",
        )
        call_names = [name for name, _, _ in ordering.mock_calls]
        assert call_names.index("cleanup_clone") < call_names.index("update_status")


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
    """SVC-PUB-09, SVC-PUB-10: Device drain tests"""

    @pytest.mark.asyncio
    async def test_drain_waits_for_sessions(self):
        """SVC-PUB-09: Graceful device drain waits for sessions"""
        with patch.object(
            DefaultPublishService,
            "_get_active_sessions",
            new_callable=AsyncMock,
            return_value=0,
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

    @pytest.mark.asyncio
    async def test_drain_timeout(self):
        """SVC-PUB-10: Drain timeout enforcement (default 30s)"""
        # Simulate sessions that never complete
        with patch.object(
            DefaultPublishService,
            "_get_active_sessions",
            new_callable=AsyncMock,
            return_value=5,
        ):
            result = await _publish_service_instance._drain_device(
                tenant="test_tenant",
                device_id=1,
                timeout_seconds=1,  # Short timeout for test
                check_interval=0.1,
            )

            assert result.success is False  # Timed out
            assert result.sessions_remaining == 5
            assert result.timeout_reached is True
            assert result.duration_seconds >= 0.5


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
