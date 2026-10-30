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
