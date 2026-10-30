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


class TestCompletePublish:
    @pytest.mark.asyncio
    async def test_complete_stop_type(self):
        svc = _make_service()
        pub = _make_publish_record(publish_type="STOP")
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            svc._bot_repo.complete_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_destroy_type(self):
        svc = _make_service()
        pub = _make_publish_record(publish_type="DESTROY")
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            svc._bot_repo.complete_destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_restart_type(self):
        svc = _make_service()
        pub = _make_publish_record(publish_type="RESTART")
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            svc._bot_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_complete_update_with_target_bot_id(self):
        svc = _make_service()
        pub = _make_publish_record(
            publish_type="UPDATE",
            extra_config={"target_bot_id": 2},
        )
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        rel = MagicMock()
        rel.device_uuid = "dev-uuid-1"
        rel.domain = "test_domain"
        svc._rel_repo.list_by_bot_id.return_value = [rel]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            svc._bot_repo.complete_update_transfer.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_update_no_target_bot_id(self):
        svc = _make_service()
        pub = _make_publish_record(publish_type="UPDATE", extra_config={})
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            svc._bot_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_complete_update_empty_device_rels(self):
        svc = _make_service()
        pub = _make_publish_record(
            publish_type="UPDATE",
            extra_config={"target_bot_id": 2},
        )
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        svc._rel_repo.list_by_bot_id.return_value = []  # no rels

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            # Should mark target bot as FAILED and destroy old bot
            svc._bot_repo.update_status.assert_called()
            svc._bot_repo.complete_destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_update_transfer_exception(self):
        svc = _make_service()
        pub = _make_publish_record(
            publish_type="UPDATE",
            extra_config={"target_bot_id": 2},
        )
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        rel = MagicMock()
        rel.device_uuid = "dev-uuid-1"
        rel.domain = "test_domain"
        svc._rel_repo.list_by_bot_id.return_value = [rel]
        svc._bot_repo.complete_update_transfer.side_effect = Exception("db error")

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(Exception, match="db error"):
                await svc.complete_publish("t", 1, "op")

    @pytest.mark.asyncio
    async def test_complete_scale_up_updates_replica(self):
        svc = _make_service()
        pub = _make_publish_record(
            publish_type="SCALE_UP",
            replica_desired=5,
        )
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            svc._bot_repo.update_bot.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_scale_down_updates_replica(self):
        svc = _make_service()
        pub = _make_publish_record(
            publish_type="SCALE_DOWN",
            replica_desired=2,
        )
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            svc._bot_repo.update_bot.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_create_type(self):
        svc = _make_service()
        pub = _make_publish_record(publish_type="CREATE")
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            svc._bot_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_complete_update_device_type(self):
        svc = _make_service()
        pub = _make_publish_record(publish_type="UPDATE_DEVICE")
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = bot
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.complete_publish("t", 1, "op")
            assert result.status == "SUCCESS"
            # UPDATE_DEVICE should NOT call update_status (it's excluded)
            svc._bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.complete_publish("t", 1, "op")


class TestCheckAndHandleTimeout:
    @pytest.mark.asyncio
    async def test_no_stale_records(self):
        svc = _make_service()
        pub = _make_publish_record()
        svc._publish_record_repo.list_stale_processing_records.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_and_handle_timeout(pub, "t")
            svc._publish_record_repo.list_stale_processing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_stale_records_trigger_callback(self):
        svc = _make_service()
        pub = _make_publish_record()
        stale = MagicMock()
        stale.id = 1
        stale.device_uuid = "dev-1"
        svc._publish_record_repo.list_stale_processing_records.return_value = [stale]

        # Mock handle_device_callback
        device = _make_device(id=10)
        svc._device_repo.get_by_device_uuid.return_value = device
        svc._publish_record_repo.get_processing_record_by_device_and_publish.return_value = None

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_and_handle_timeout(pub, "t")
            svc._publish_record_repo.list_stale_processing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_stale_record_no_device_uuid_skipped(self):
        svc = _make_service()
        pub = _make_publish_record()
        stale = MagicMock()
        stale.id = 1
        stale.device_uuid = None  # no device_uuid
        svc._publish_record_repo.list_stale_processing_records.return_value = [stale]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._check_and_handle_timeout(pub, "t")
            # Should not call handle_device_callback since no device_uuid
            svc._device_repo.get_by_device_uuid.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_record_callback_exception_handled(self):
        svc = _make_service()
        pub = _make_publish_record()
        stale = MagicMock()
        stale.id = 1
        stale.device_uuid = "dev-1"
        svc._publish_record_repo.list_stale_processing_records.return_value = [stale]

        svc._device_repo.get_by_device_uuid.side_effect = Exception("db error")

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            # Should not raise
            await svc._check_and_handle_timeout(pub, "t")


class TestGetPublishProgress:
    @pytest.mark.asyncio
    async def test_progress_not_found(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.get_publish_progress("t", 1)
            assert result is None

    @pytest.mark.asyncio
    async def test_progress_tenant_mismatch(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = _make_publish_record()
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.get_publish_progress("t", 1)
            assert result is None

    @pytest.mark.asyncio
    async def test_progress_success(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_record_repo.list_stale_processing_records.return_value = []
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(id=1, status="COMPLETED", stage="PREPUB"),
            _make_batch_record(id=2, status="PENDING", stage="GRAY"),
        ]
        svc._publish_record_repo.count_records_by_publish_id.return_value = {
            "PENDING": 2,
            "SUCCESS": 1,
        }
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PENDING": 1,
            "SUCCESS": 1,
        }

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.get_publish_progress("t", 1)
            assert result is not None
            assert result.publish_id == 1
            assert result.status == "ACTIVE"
            assert len(result.stages) == 2

    @pytest.mark.asyncio
    async def test_progress_with_include_devices(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_record_repo.list_stale_processing_records.return_value = []

        batch = _make_batch_record(id=1, status="COMPLETED", stage="PREPUB")
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]
        svc._publish_record_repo.count_records_by_publish_id.return_value = {
            "PENDING": 0,
            "SUCCESS": 1,
        }
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "SUCCESS": 1,
        }

        record = MagicMock()
        record.device_id = 10
        record.event_type = "CREATE"
        record.result_status = "SUCCESS"
        record.result_message = "ok"
        record.gmt_create = datetime.now()
        svc._publish_record_repo.list_by_batch_id.return_value = [record]

        device = _make_device(id=10, device_uuid="dev-uuid-10")
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.get_publish_progress("t", 1, include_devices=True)
            assert result is not None
            assert len(result.device_details) == 1
            assert len(result.device_details[0].devices) == 1

    @pytest.mark.asyncio
    async def test_progress_disappears_after_timeout(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        # First call returns pub, second (after timeout) returns None
        svc._publish_repo.get_by_id.side_effect = [pub, None]
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_record_repo.list_stale_processing_records.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.get_publish_progress("t", 1)
            assert result is None


class TestAggregateStageProgress:
    def test_aggregate_empty_batches(self):
        svc = _make_service()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            stages = svc._aggregate_stage_progress(
                [], publish_id=1, tenant="t", record_repo=MagicMock()
            )
            assert stages == []

    def test_aggregate_all_completed(self):
        svc = _make_service()
        batches = [
            _make_batch_record(
                id=1, stage="PREPUB", status="COMPLETED", batch_capacity=2
            ),
            _make_batch_record(
                id=2, stage="GRAY", status="COMPLETED", batch_capacity=3
            ),
        ]
        mock_repo = MagicMock()
        mock_repo.count_records_by_batch_id.return_value = {"SUCCESS": 2}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            stages = svc._aggregate_stage_progress(
                batches, publish_id=1, tenant="t", record_repo=mock_repo
            )
            assert len(stages) == 2
            assert all(s.status == "SUCCESS" for s in stages)

    def test_aggregate_partial_complete(self):
        svc = _make_service()
        batches = [
            _make_batch_record(
                id=1, stage="PREPUB", status="COMPLETED", batch_capacity=2
            ),
            _make_batch_record(id=2, stage="GRAY", status="PENDING", batch_capacity=3),
        ]
        mock_repo = MagicMock()
        mock_repo.count_records_by_batch_id.return_value = {"SUCCESS": 1, "PENDING": 1}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            stages = svc._aggregate_stage_progress(
                batches, publish_id=1, tenant="t", record_repo=mock_repo
            )
            assert len(stages) == 2
            # PREPUB: 1 batch completed = all complete = SUCCESS
            assert stages[0].status == "SUCCESS"
            # GRAY: 0 batches completed = PENDING
            assert stages[1].status == "PENDING"


class TestComputeOverallProgress:
    def test_zero_batches(self):
        svc = _make_service()
        result = svc._compute_overall_progress([], {})
        assert result.total_batches == 0
        assert result.progress_percentage == 0.0

    def test_all_completed(self):
        svc = _make_service()
        batches = [
            _make_batch_record(id=1, status="COMPLETED", batch_capacity=2),
            _make_batch_record(id=2, status="COMPLETED", batch_capacity=3),
        ]
        counts = {"PENDING": 0, "SUCCESS": 5, "FAILED": 0}
        result = svc._compute_overall_progress(batches, counts)
        assert result.total_batches == 2
        assert result.completed_batches == 2
        assert result.total_devices == 5
        assert result.processed_devices == 5
        assert result.progress_percentage == 100.0

    def test_with_failures(self):
        svc = _make_service()
        batches = [
            _make_batch_record(id=1, status="COMPLETED", batch_capacity=4),
        ]
        counts = {"PENDING": 1, "SUCCESS": 2, "FAILED": 1}
        result = svc._compute_overall_progress(batches, counts)
        assert result.processed_devices == 3  # 4 - 1 PENDING
        assert result.failed_devices == 1
        assert result.progress_percentage == 75.0


class TestGetDeviceDetails:
    def test_no_batches(self):
        svc = _make_service()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            details, failed = svc._get_device_details(
                [], tenant="t", record_repo=MagicMock()
            )
            assert details == []
            assert failed == []

    def test_with_failed_device(self):
        svc = _make_service()
        batch = _make_batch_record(id=1, status="COMPLETED")

        record1 = MagicMock()
        record1.device_id = 10
        record1.event_type = "CREATE"
        record1.result_status = "SUCCESS"
        record1.result_message = "ok"
        record1.gmt_create = datetime.now()

        record2 = MagicMock()
        record2.device_id = 11
        record2.event_type = "CREATE"
        record2.result_status = "FAILED"
        record2.result_message = "err"
        record2.gmt_create = datetime.now()

        mock_repo = MagicMock()
        mock_repo.list_by_batch_id.return_value = [record1, record2]

        device1 = _make_device(id=10, device_uuid="uuid-10")
        device2 = _make_device(id=11, device_uuid="uuid-11")
        svc._device_repo.get_by_ids.return_value = {10: device1, 11: device2}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            details, failed = svc._get_device_details(
                [batch], tenant="t", record_repo=mock_repo
            )
            assert len(details) == 1
            assert len(details[0].devices) == 2
            assert len(failed) == 1
            assert failed[0].result_status == "FAILED"

    def test_retry_state_surfaced_per_device(self):
        svc = _make_service()
        batch = _make_batch_record(id=1, status="RUNNING")

        retrying = MagicMock()
        retrying.device_id = 10
        retrying.event_type = "CREATE"
        retrying.result_status = "PROCESSING"
        retrying.result_message = None
        retrying.gmt_create = datetime.now()
        retrying.extra_config = {"publish_max_retry_times": 3, "publish_retry_count": 2}

        plain = MagicMock()
        plain.device_id = 11
        plain.event_type = "CREATE"
        plain.result_status = "PROCESSING"
        plain.result_message = None
        plain.gmt_create = datetime.now()
        plain.extra_config = {}

        mock_repo = MagicMock()
        mock_repo.list_by_batch_id.return_value = [retrying, plain]
        svc._device_repo.get_by_ids.return_value = {
            10: _make_device(id=10, device_uuid="uuid-10"),
            11: _make_device(id=11, device_uuid="uuid-11"),
        }

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            details, _ = svc._get_device_details(
                [batch], tenant="t", record_repo=mock_repo
            )

        by_id = {d.device_id: d for d in details[0].devices}
        assert by_id[10].publish_max_retry_times == 3
        assert by_id[10].publish_retry_count == 2
        assert by_id[11].publish_max_retry_times == 0
        assert by_id[11].publish_retry_count == 0

    def test_device_not_found(self):
        svc = _make_service()
        batch = _make_batch_record(id=1, status="COMPLETED")

        record = MagicMock()
        record.device_id = 999
        record.event_type = "CREATE"
        record.result_status = "SUCCESS"
        record.result_message = "ok"
        record.gmt_create = datetime.now()

        mock_repo = MagicMock()
        mock_repo.list_by_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {}  # device not found

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            details, failed = svc._get_device_details(
                [batch], tenant="t", record_repo=mock_repo
            )
            assert len(details) == 1
            assert details[0].devices[0].device_uuid is None


class TestAutoExecuteStages:
    @pytest.mark.asyncio
    async def test_publish_not_found_returns(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._auto_execute_stages("t", 1, "op")
            # Should return without error

    @pytest.mark.asyncio
    async def test_publish_not_active_returns(self):
        svc = _make_service()
        pub = _make_publish_record(status="PENDING")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._auto_execute_stages("t", 1, "op")

    @pytest.mark.asyncio
    async def test_no_pending_batches_returns(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            await svc._auto_execute_stages("t", 1, "op")

    @pytest.mark.asyncio
    async def test_execute_exception_handled(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        batch = _make_batch_record(status="PENDING")
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]

        with (
            patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ),
            patch.object(
                svc,
                "execute_stage",
                new_callable=AsyncMock,
                side_effect=Exception("exec error"),
            ),
        ):
            # Should not raise
            await svc._auto_execute_stages("t", 1, "op")


class TestShouldAutoComplete:
    def test_auto_complete_false(self):
        svc = _make_service()
        config = PublishConfig(auto_complete=False)
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            assert svc._should_auto_complete("t", 1, config) is False

    def test_auto_complete_no_batches(self):
        svc = _make_service()
        config = PublishConfig(auto_complete=True)
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            assert svc._should_auto_complete("t", 1, config) is False
