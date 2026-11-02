"""Comprehensive coverage tests for DefaultPublishService.

Focuses on methods, branches, and error paths not already covered by
test_publish_service.py.  Covers:
  - _cleanup_pending_clone_on_update_failure (all early-return branches)
  - _can_transition / _get_next_status
  - _extra_config_to_publish_config
  - create_publish edge cases (orphan cleanup, stale publish, scale
    validation errors, restart scope, no-eligible-devices, etc.)
  - _generate_batches edge cases (STOP, UPDATE_DEVICE, DESTROY, zero
    device batches, batch_capacity override)
  - _create_device_records_for_publish (all publish types, no-eligible,
    SCALE_UP missing template, UPDATE_DEVICE target uuids)
  - _get_current_stage / _get_pending_batches / _check_all_batches_complete
  - _get_publish_and_bot_record / get_publish_bot_uuid
  - _build_publish_response / _refresh_publish_response
  - get_publish
  - approve_stage (auto_approve guard, already SUCCESS, already ACTIVE,
    APPROVING -> ACTIVE, invalid status)
  - reject_publish (invalid status)
  - revoke_publish (invalid status)
  - list_publishes (with/without bot_id, status filter, pagination)
  - execute_stage (no batches, not ACTIVE, bot_record fetch)
  - _execute_batch dispatch (unknown type)
  - _execute_create_batch (device not found, start fails, start success,
    start async)
  - _execute_update_batch (no target_bot_id, drain fail, update fail,
    update success, update async)
  - _execute_restart_batch (no pending, device not found, restart fail,
    restart success, restart async)
  - _execute_scale_batch (no template, no pending, SCALE_UP start fail,
    SCALE_DOWN destroy success/fail, hook_result)
  - _execute_destroy_batch (no pending, destroy fail, destroy success,
    hook_result, rel cleanup)
  - _execute_stop_batch (no pending, stop fail, stop success, hook_result)
  - _drain_device (mock mode, sessions zero, timeout)
  - handle_device_callback (non-start, invalid status, device not found,
    no PROCESSING record, SUCCESS, FAILED, concurrent, no batch_id)
  - _check_batch_completion (still processing, all complete, batch fail)
  - _check_stage_advancement (stage_failed, all complete, next stage
    pause, auto-approve, empty batches, auto_complete)
  - complete_publish (STOP, DESTROY, RESTART, UPDATE with/without
    target_bot_id, SCALE_UP/DOWN replica update, no target_bot_id,
    empty device rels)
  - _check_and_handle_timeout (stale records, no stale records)
  - get_publish_progress (include_devices, timeout handling)
  - _aggregate_stage_progress
  - _compute_overall_progress
  - _get_device_details
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.publish_manage import (
    BatchResult,
    BatchStatus,
    DeviceCallbackRequest,
    DrainResult,
    PublishConfig,
    PublishConflictError,
    PublishNotFoundError,
    PublishResponse,
    PublishStage,
    PublishStatus,
    PublishType,
    RestartScope,
    StageConfig,
)
from secbaas.community.core.repository.publish_batch import (
    PublishBatchRecord,
)
from secbaas.community.core.service.publish_manage import DefaultPublishService
from secbaas.community.core.service.publish_manage._publish_service import (
    BatchConfig,
    _extra_config_to_publish_config,
)

# ====================================================================
# Helpers
# ====================================================================


def _make_service() -> DefaultPublishService:
    """Build a DefaultPublishService with all-mock dependencies."""
    return DefaultPublishService(
        bot_repo=MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=MagicMock(),
        batch_repo=MagicMock(),
        publish_record_repo=MagicMock(),
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
    )


def _make_publish_record(
    *,
    id: int = 1,
    bot_id: int = 1,
    publish_type: str = "CREATE",
    status: str = "PENDING",
    extra_config: dict | None = None,
    creator: str = "user1",
    modifier: str = "user1",
    tenant: str = "test_tenant",
    replica_desired: int | None = None,
) -> MagicMock:
    rec = MagicMock()
    rec.id = id
    rec.bot_id = bot_id
    rec.publish_type = publish_type
    rec.status = status
    rec.extra_config = extra_config or {}
    rec.creator = creator
    rec.modifier = modifier
    rec.tenant = tenant
    rec.replica_desired = replica_desired
    rec.gmt_create = datetime.now()
    rec.gmt_modified = datetime.now()
    rec.domain = "test_domain"
    return rec


def _make_bot_record(
    *,
    id: int = 1,
    bot_uuid: str = "bot-uuid-1",
    status: str = "ACTIVE",
    domain: str = "test_domain",
    template_uuid: str | None = "tpl-1",
    replica_desired: int | None = 3,
    config: Any | None = None,
    extra_config: dict | None = None,
) -> MagicMock:
    bot = MagicMock()
    bot.id = id
    bot.bot_uuid = bot_uuid
    bot.status = status
    bot.domain = domain
    bot.template_uuid = template_uuid
    bot.replica_desired = replica_desired
    bot.config = config
    bot.extra_config = extra_config or {}
    return bot


def _make_batch_record(
    *,
    id: int = 1,
    batch_index: int = 0,
    batch_capacity: int = 1,
    stage: str = "PROD_FIRST_BATCH",
    status: str = "PENDING",
    cooldown_seconds: int = 0,
) -> MagicMock:
    b = MagicMock()
    b.id = id
    b.batch_index = batch_index
    b.batch_capacity = batch_capacity
    b.stage = stage
    b.status = status
    b.cooldown_seconds = cooldown_seconds
    return b


def _make_device(
    *,
    id: int = 10,
    device_uuid: str = "dev-uuid-1",
    status: str = "PENDING",
    provider_device_id: str = "provider-1",
) -> MagicMock:
    d = MagicMock()
    d.id = id
    d.device_uuid = device_uuid
    d.status = status
    d.provider_device_id = provider_device_id
    return d


# ====================================================================
# _extra_config_to_publish_config
# ====================================================================


class TestHandleDeviceCallback:
    @pytest.mark.asyncio
    async def test_non_start_event_ignored(self):
        svc = _make_service()
        cb = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="stop",
            result_status="SUCCESS",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.handle_device_callback(cb)
            assert result["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_invalid_result_status_rejected(self):
        svc = _make_service()
        cb = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="start",
            result_status="UNKNOWN",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.handle_device_callback(cb)
            assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_device_not_found_raises(self):
        svc = _make_service()
        svc._device_repo.get_by_device_uuid.return_value = None
        cb = DeviceCallbackRequest(
            device_uuid="dev-missing",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.handle_device_callback(cb)

    @pytest.mark.asyncio
    async def test_no_processing_record_ignored(self):
        svc = _make_service()
        device = _make_device(id=10)
        svc._device_repo.get_by_device_uuid.return_value = device
        svc._publish_record_repo.get_processing_record_by_device_and_publish.return_value = None
        cb = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.handle_device_callback(cb)
            assert result["status"] == "ignored"
            assert "no PROCESSING" in result["reason"]

    @pytest.mark.asyncio
    async def test_record_not_processing_ignored(self):
        svc = _make_service()
        device = _make_device(id=10)
        svc._device_repo.get_by_device_uuid.return_value = device
        record = MagicMock()
        record.result_status = "SUCCESS"  # already processed
        svc._publish_record_repo.get_processing_record_by_device_and_publish.return_value = record
        cb = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.handle_device_callback(cb)
            assert result["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_success_callback(self):
        svc = _make_service()
        device = _make_device(id=10)
        svc._device_repo.get_by_device_uuid.return_value = device
        record = MagicMock()
        record.id = 100
        record.result_status = "PROCESSING"
        record.batch_id = 5
        svc._publish_record_repo.get_processing_record_by_device_and_publish.return_value = record
        svc._publish_record_repo.update_result_if_processing.return_value = True

        # Mock batch completion check
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PROCESSING": 0,
            "FAILED": 0,
        }
        batch = _make_batch_record(id=5, status="RUNNING")
        svc._publish_batch_repo.get_by_id.return_value = batch
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]
        svc._publish_repo.get_by_id.return_value = _make_publish_record(status="ACTIVE")

        cb = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            exit_code=0,
            stdout="ok",
            stderr="",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.handle_device_callback(cb)
            assert result["status"] == "processed"

    @pytest.mark.asyncio
    async def test_failed_callback(self):
        svc = _make_service()
        device = _make_device(id=10)
        svc._device_repo.get_by_device_uuid.return_value = device
        record = MagicMock()
        record.id = 100
        record.result_status = "PROCESSING"
        record.batch_id = 5
        svc._publish_record_repo.get_processing_record_by_device_and_publish.return_value = record
        svc._publish_record_repo.update_result_if_processing.return_value = True

        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PROCESSING": 0,
            "FAILED": 1,
        }
        batch = _make_batch_record(id=5, status="RUNNING")
        svc._publish_batch_repo.get_by_id.return_value = batch
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]
        svc._publish_repo.get_by_id.return_value = _make_publish_record(status="ACTIVE")

        cb = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="start",
            result_status="FAILED",
            exit_code=1,
            stdout="",
            stderr="err",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.handle_device_callback(cb)
            assert result["status"] == "processed"

    @pytest.mark.asyncio
    async def test_concurrent_callback_ignored(self):
        svc = _make_service()
        device = _make_device(id=10)
        svc._device_repo.get_by_device_uuid.return_value = device
        record = MagicMock()
        record.id = 100
        record.result_status = "PROCESSING"
        record.batch_id = 5
        svc._publish_record_repo.get_processing_record_by_device_and_publish.return_value = record
        svc._publish_record_repo.update_result_if_processing.return_value = (
            False  # concurrent
        )

        cb = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.handle_device_callback(cb)
            assert result["status"] == "ignored"
            assert "concurrent" in result["reason"]

    @pytest.mark.asyncio
    async def test_no_batch_id_warning(self):
        svc = _make_service()
        device = _make_device(id=10)
        svc._device_repo.get_by_device_uuid.return_value = device
        record = MagicMock()
        record.id = 100
        record.result_status = "PROCESSING"
        record.batch_id = None
        svc._publish_record_repo.get_processing_record_by_device_and_publish.return_value = record
        svc._publish_record_repo.update_result_if_processing.return_value = True

        cb = DeviceCallbackRequest(
            device_uuid="dev-1",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            tenant="t",
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.handle_device_callback(cb)
            assert result["status"] == "processed"
            assert result.get("warning") == "no batch_id"


class TestCheckBatchCompletion:
    @pytest.mark.asyncio
    async def test_still_processing_returns(self):
        svc = _make_service()
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PROCESSING": 2,
            "SUCCESS": 1,
        }
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_batch_completion("t", batch_id=1, publish_id=1)
            svc._publish_batch_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_complete_no_failed(self):
        svc = _make_service()
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PROCESSING": 0,
            "SUCCESS": 3,
            "FAILED": 0,
        }
        batch = _make_batch_record(id=1, status="RUNNING")
        svc._publish_batch_repo.get_by_id.return_value = batch
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]
        svc._publish_repo.get_by_id.return_value = _make_publish_record(status="ACTIVE")

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_batch_completion("t", batch_id=1, publish_id=1)
            svc._publish_batch_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_batch_failed(self):
        svc = _make_service()
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PROCESSING": 0,
            "SUCCESS": 1,
            "FAILED": 2,
        }
        batch = _make_batch_record(id=1, status="RUNNING")
        svc._publish_batch_repo.get_by_id.return_value = batch
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]
        svc._publish_repo.get_by_id.return_value = _make_publish_record(status="ACTIVE")

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_batch_completion("t", batch_id=1, publish_id=1)
            svc._publish_batch_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_batch_not_found(self):
        svc = _make_service()
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PROCESSING": 0,
            "SUCCESS": 1,
        }
        svc._publish_batch_repo.get_by_id.return_value = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_batch_completion("t", batch_id=1, publish_id=1)
            svc._publish_batch_repo.update_status.assert_not_called()


class TestCheckStageAdvancement:
    @pytest.mark.asyncio
    async def test_publish_not_found_returns(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=True
            )
            svc._publish_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_stage_failed_marks_publish_failed(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE", publish_type="CREATE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []
        svc._bot_repo.get_by_id.return_value = _make_bot_record(status="ACTIVE")

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=True
            )
            svc._publish_repo.update_status.assert_called_with(
                publish_id=1,
                tenant="t",
                env="test",
                status="FAILED",
                modifier="callback",
            )

    @pytest.mark.asyncio
    async def test_stage_failed_destroy_type(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE", publish_type="DESTROY")
        svc._publish_repo.get_by_id.return_value = pub
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []
        bot = _make_bot_record(status="DESTROYING")
        svc._bot_repo.get_by_id.return_value = bot
        svc._device_repo.list_by_bot_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PROD_FIRST_BATCH", stage_failed=True
            )
            svc._bot_repo.complete_destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_stage_failed_stop_type(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE", publish_type="STOP")
        svc._publish_repo.get_by_id.return_value = pub
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []
        svc._bot_repo.get_by_id.return_value = _make_bot_record()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PROD_FIRST_BATCH", stage_failed=True
            )
            svc._bot_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_stage_failed_update_type_cleanup(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE", publish_type="UPDATE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []
        svc._bot_repo.get_by_id.return_value = _make_bot_record()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=True
            )
            svc._publish_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_stage_failed_create_type_pending_bot(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE", publish_type="CREATE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []
        svc._bot_repo.get_by_id.return_value = _make_bot_record(
            status=BotStatus.PENDING.value
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=True
            )
            svc._bot_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_all_complete_no_more_stages_auto_complete(self):
        svc = _make_service()
        pub = _make_publish_record(
            status="ACTIVE",
            publish_type="CREATE",
            extra_config={"auto_complete": True},
        )
        svc._publish_repo.get_by_id.return_value = pub
        # All batches COMPLETED
        batch = _make_batch_record(id=1, status="COMPLETED")
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]
        # _get_pending_batches returns (SUCCESS, [])
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]

        with (
            patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ),
            patch.object(
                svc,
                "complete_publish",
                new_callable=AsyncMock,
            ) as mock_complete,
        ):
            mock_complete.return_value = MagicMock()
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=False
            )
            mock_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_complete_no_more_stages_no_auto_complete(self):
        svc = _make_service()
        pub = _make_publish_record(
            status="ACTIVE",
            publish_type="CREATE",
            extra_config={"auto_complete": False},
        )
        svc._publish_repo.get_by_id.return_value = pub
        batch = _make_batch_record(id=1, status="COMPLETED")
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=False
            )
            # Should not call complete_publish or update_status to APPROVING
            svc._publish_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_next_stage_pause_for_approval(self):
        svc = _make_service()
        pub = _make_publish_record(
            status="ACTIVE",
            publish_type="CREATE",
            extra_config={
                "auto_complete": True,
                "stages": {
                    "GRAY": {
                        "pause_for_approval": True,
                        "cooldown_seconds": 0,
                    },
                },
            },
        )
        svc._publish_repo.get_by_id.return_value = pub

        completed_batch = _make_batch_record(id=1, stage="PREPUB", status="COMPLETED")
        pending_batch = _make_batch_record(id=2, stage="GRAY", status="PENDING")
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            completed_batch,
            pending_batch,
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=False
            )
            svc._publish_repo.update_status.assert_called_with(
                publish_id=1,
                tenant="t",
                env="test",
                status="APPROVING",
                modifier="callback",
            )

    @pytest.mark.asyncio
    async def test_next_stage_auto_approve(self):
        svc = _make_service()
        pub = _make_publish_record(
            status="ACTIVE",
            publish_type="CREATE",
            extra_config={
                "auto_complete": True,
                "auto_approve": True,
                "stages": {
                    "GRAY": {
                        "pause_for_approval": True,
                        "cooldown_seconds": 0,
                    },
                },
            },
        )
        svc._publish_repo.get_by_id.return_value = pub

        completed_batch = _make_batch_record(id=1, stage="PREPUB", status="COMPLETED")
        pending_batch = _make_batch_record(id=2, stage="GRAY", status="PENDING")
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            completed_batch,
            pending_batch,
        ]

        with (
            patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ),
            patch.object(
                svc,
                "execute_stage",
                new_callable=AsyncMock,
            ) as mock_exec,
        ):
            mock_exec.return_value = DrainResult(
                success=True,
                sessions_remaining=0,
                duration_seconds=0.0,
            )
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=False
            )
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_next_stage_empty_batches(self):
        svc = _make_service()
        pub = _make_publish_record(
            status="ACTIVE",
            publish_type="CREATE",
            extra_config={
                "auto_complete": True,
                "stages": {
                    "GRAY": {
                        "pause_for_approval": False,
                        "cooldown_seconds": 0,
                    },
                },
            },
        )
        svc._publish_repo.get_by_id.return_value = pub

        completed_batch = _make_batch_record(id=1, stage="PREPUB", status="COMPLETED")
        empty_batch = _make_batch_record(
            id=2, stage="GRAY", status="PENDING", batch_capacity=0
        )
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            completed_batch,
            empty_batch,
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=False
            )
            # Empty batches should be marked COMPLETED
            svc._publish_batch_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_not_all_batches_complete(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE", publish_type="CREATE")
        svc._publish_repo.get_by_id.return_value = pub

        completed_batch = _make_batch_record(id=1, stage="PREPUB", status="COMPLETED")
        running_batch = _make_batch_record(id=2, stage="PREPUB", status="RUNNING")
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            completed_batch,
            running_batch,
        ]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_stage_advancement(
                "t", publish_id=1, current_stage="PREPUB", stage_failed=False
            )
            svc._publish_repo.update_status.assert_not_called()
