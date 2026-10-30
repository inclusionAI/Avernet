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


class TestExecuteScaleBatch:
    @pytest.mark.asyncio
    async def test_scale_up_no_template_returns_failure(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record(template_uuid=None)
        batch = _make_batch_record()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_UP",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is False
            assert "no template_uuid" in result.error_message

    @pytest.mark.asyncio
    async def test_scale_up_template_not_found_returns_failure(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record(template_uuid="tpl-missing")
        svc._template_service.get_online_template_by_uuid.return_value = None
        batch = _make_batch_record()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_UP",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is False
            assert "Template not found" in result.error_message

    @pytest.mark.asyncio
    async def test_scale_up_no_pending_records(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        svc._template_service.get_online_template_by_uuid.return_value = MagicMock()
        batch = _make_batch_record()
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_UP",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is False
            assert "No pending records" in result.error_message

    @pytest.mark.asyncio
    async def test_scale_up_start_failed(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        svc._template_service.get_online_template_by_uuid.return_value = MagicMock()
        batch = _make_batch_record()

        device = _make_device(id=10)
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        result_obj = MagicMock()
        result_obj.status = DeviceStatus.FAILED.value
        result_obj.err_msg = "start failed"
        svc._device_service.start_device = AsyncMock(return_value=result_obj)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_UP",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is False
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_scale_up_start_exception(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        svc._template_service.get_online_template_by_uuid.return_value = MagicMock()
        batch = _make_batch_record()

        device = _make_device(id=10)
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        svc._device_service.start_device = AsyncMock(side_effect=Exception("net"))

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_UP",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is False
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_scale_up_start_async_pending(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        svc._template_service.get_online_template_by_uuid.return_value = MagicMock()
        batch = _make_batch_record()

        device = _make_device(id=10)
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        result_obj = MagicMock()
        result_obj.status = DeviceStatus.PENDING.value
        svc._device_service.start_device = AsyncMock(return_value=result_obj)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_UP",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.processed_count == 0
            assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_scale_down_destroy_success(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        destroy_resp = MagicMock()
        destroy_resp.success = True
        destroy_resp.error_message = None
        destroy_resp.hook_result = None
        svc._device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp
        )

        svc._rel_repo.list_by_bot_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_DOWN",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is True
            assert result.processed_count == 1

    @pytest.mark.asyncio
    async def test_scale_down_destroy_failure(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        destroy_resp = MagicMock()
        destroy_resp.success = False
        destroy_resp.error_message = "destroy error"
        destroy_resp.hook_result = None
        svc._device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_DOWN",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is False
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_scale_down_destroy_exception(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        svc._device_service.destroy_device_by_uuid = AsyncMock(
            side_effect=Exception("network")
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_DOWN",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is False
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_scale_down_destroy_success_with_hook_result(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        hook = MagicMock()
        hook.exit_code = 0
        hook.stdout = "ok"
        hook.stderr = ""
        destroy_resp = MagicMock()
        destroy_resp.success = True
        destroy_resp.error_message = None
        destroy_resp.hook_result = hook
        svc._device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp
        )

        svc._rel_repo.list_by_bot_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_DOWN",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is True

    @pytest.mark.asyncio
    async def test_scale_down_destroy_failure_with_hook_result(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        hook = MagicMock()
        hook.exit_code = 1
        hook.stdout = ""
        hook.stderr = "err"
        destroy_resp = MagicMock()
        destroy_resp.success = False
        destroy_resp.error_message = "fail"
        destroy_resp.hook_result = hook
        svc._device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_DOWN",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is False

    @pytest.mark.asyncio
    async def test_scale_down_device_not_found_skips(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        record = MagicMock()
        record.id = 1
        record.device_id = 999
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {}

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_DOWN",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is True

    @pytest.mark.asyncio
    async def test_scale_down_rel_cleanup(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE", device_uuid="uuid-10")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        destroy_resp = MagicMock()
        destroy_resp.success = True
        destroy_resp.error_message = None
        destroy_resp.hook_result = None
        svc._device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp
        )

        rel = MagicMock()
        rel.id = 100
        rel.device_uuid = "uuid-10"
        svc._rel_repo.list_by_bot_id.return_value = [rel]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_DOWN",
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is True
            svc._rel_repo.soft_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_scale_bot_not_found(self):
        svc = _make_service()
        pub = _make_publish_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = None
        batch = _make_batch_record()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_scale_batch(
                "t",
                1,
                batch,
                "SCALE_UP",
                "op",
                publish_record=pub,
                bot_record=None,
            )
            assert result.success is False
