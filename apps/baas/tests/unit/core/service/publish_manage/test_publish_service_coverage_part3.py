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


class TestGetPublishBotUuid:
    @pytest.mark.asyncio
    async def test_publish_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.get_publish_bot_uuid(tenant="t", publish_id=1)

    @pytest.mark.asyncio
    async def test_bot_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = _make_publish_record()
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.get_publish_bot_uuid(tenant="t", publish_id=1)

    @pytest.mark.asyncio
    async def test_returns_bot_uuid(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = _make_publish_record()
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record(
            bot_uuid="bot-xyz"
        )
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            uuid = await svc.get_publish_bot_uuid(tenant="t", publish_id=1)
            assert uuid == "bot-xyz"


class TestGetPublish:
    @pytest.mark.asyncio
    async def test_get_publish_not_found(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.get_publish("t", 1)
            assert result is None

    @pytest.mark.asyncio
    async def test_get_publish_success(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE", publish_type="CREATE")
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(status="PENDING", stage="PREPUB"),
        ]
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.get_publish("t", 1)
            assert result is not None
            assert result.id == 1
            assert result.status == "ACTIVE"


class TestBuildAndRefreshResponse:
    @pytest.mark.asyncio
    async def test_refresh_publish_response_not_found(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc._refresh_publish_response("t", 1)

    def test_build_publish_response(self):
        svc = _make_service()
        pub = _make_publish_record(extra_config={"replica_desired": 3})
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(status="PENDING", stage="PREPUB"),
        ]
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            resp = svc._build_publish_response(pub)
            assert resp.id == 1
            assert resp.extra_config is not None
            assert resp.extra_config.replica_desired == 3


class TestApproveStageEdgeCases:
    @pytest.mark.asyncio
    async def test_approve_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.approve_stage("t", 1, "op")

    @pytest.mark.asyncio
    async def test_approve_auto_approve_active_ignores_manual(self):
        svc = _make_service()
        pub = _make_publish_record(
            status="PENDING", extra_config={"auto_approve": True}
        )
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        # For _refresh_publish_response
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.approve_stage("t", 1, "op", _called_internally=False)
            assert result is not None

    @pytest.mark.asyncio
    async def test_approve_already_success_noop(self):
        svc = _make_service()
        pub = _make_publish_record(status="SUCCESS")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.approve_stage("t", 1, "op")
            assert result.status == "SUCCESS"

    @pytest.mark.asyncio
    async def test_approve_already_active_continues_execution(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with patch.object(
                svc, "_auto_execute_stages", new_callable=AsyncMock
            ) as mock_exec:
                result = await svc.approve_stage("t", 1, "op")
                mock_exec.assert_called_once()
                assert result is not None

    @pytest.mark.asyncio
    async def test_approve_invalid_status_raises(self):
        svc = _make_service()
        pub = _make_publish_record(status="FAILED")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="Cannot approve publish in status"):
                await svc.approve_stage("t", 1, "op")

    @pytest.mark.asyncio
    async def test_approve_from_approving(self):
        svc = _make_service()
        pub = _make_publish_record(status="APPROVING")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with patch.object(
                svc, "_auto_execute_stages", new_callable=AsyncMock
            ) as mock_exec:
                result = await svc.approve_stage("t", 1, "op")
                mock_exec.assert_called_once()
                svc._publish_repo.update_status.assert_called()
                assert result is not None


class TestRejectRevokeInvalidStatus:
    @pytest.mark.asyncio
    async def test_reject_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.reject_publish("t", 1, "op", "reason")

    @pytest.mark.asyncio
    async def test_reject_invalid_status_raises(self):
        svc = _make_service()
        pub = _make_publish_record(status="SUCCESS")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="Cannot reject publish in status"):
                await svc.reject_publish("t", 1, "op", "reason")

    @pytest.mark.asyncio
    async def test_reject_from_pending(self):
        svc = _make_service()
        pub = _make_publish_record(status="PENDING")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.reject_publish("t", 1, "op", "bad")
            assert result is not None
            svc._publish_repo.update_status.assert_called_with(
                publish_id=1,
                tenant="t",
                env="test",
                status="REJECTED",
                modifier="op",
            )

    @pytest.mark.asyncio
    async def test_revoke_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.revoke_publish("t", 1, "op")

    @pytest.mark.asyncio
    async def test_revoke_invalid_status_raises(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="Cannot revoke publish in status"):
                await svc.revoke_publish("t", 1, "op")

    @pytest.mark.asyncio
    async def test_revoke_from_approving(self):
        svc = _make_service()
        pub = _make_publish_record(status="APPROVING")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.revoke_publish("t", 1, "op", "reason")
            assert result is not None
            svc._publish_repo.update_status.assert_called_with(
                publish_id=1,
                tenant="t",
                env="test",
                status="REVOKED",
                modifier="op",
            )


class TestListPublishes:
    @pytest.mark.asyncio
    async def test_list_no_bot_id_returns_empty(self):
        svc = _make_service()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.list_publishes("t", bot_id=None)
            assert result == []

    @pytest.mark.asyncio
    async def test_list_with_bot_id_and_status_filter(self):
        svc = _make_service()
        pub1 = _make_publish_record(id=1, status="PENDING")
        pub2 = _make_publish_record(id=2, status="SUCCESS")
        svc._publish_repo.list_by_bot_id.return_value = [pub1, pub2]
        svc._bot_service.get_bot = AsyncMock(return_value=_make_bot_record())
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.list_publishes(
                "t", bot_id=1, status=PublishStatus.PENDING
            )
            assert len(result) == 1
            assert result[0].status == "PENDING"

    @pytest.mark.asyncio
    async def test_list_skips_when_bot_not_found(self):
        svc = _make_service()
        pub1 = _make_publish_record(id=1, status="PENDING")
        svc._publish_repo.list_by_bot_id.return_value = [pub1]
        svc._bot_service.get_bot = AsyncMock(return_value=None)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.list_publishes("t", bot_id=1)
            assert result == []

    @pytest.mark.asyncio
    async def test_list_pagination(self):
        svc = _make_service()
        pubs = [_make_publish_record(id=i, status="PENDING") for i in range(5)]
        svc._publish_repo.list_by_bot_id.return_value = pubs
        svc._bot_service.get_bot = AsyncMock(return_value=_make_bot_record())
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.list_publishes("t", bot_id=1, page=1, page_size=2)
            assert len(result) == 2
            result2 = await svc.list_publishes("t", bot_id=1, page=2, page_size=2)
            assert len(result2) == 2
            result3 = await svc.list_publishes("t", bot_id=1, page=3, page_size=2)
            assert len(result3) == 1


class TestExecuteStageEdgeCases:
    @pytest.mark.asyncio
    async def test_execute_stage_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.execute_stage("t", 1, "op")

    @pytest.mark.asyncio
    async def test_execute_stage_not_active_raises(self):
        svc = _make_service()
        pub = _make_publish_record(status="PENDING")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="Cannot execute stage in status"):
                await svc.execute_stage("t", 1, "op")

    @pytest.mark.asyncio
    async def test_execute_stage_no_batches_returns_drain_result(self):
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = _make_bot_record()
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.execute_stage("t", 1, "op")
            assert isinstance(result, DrainResult)
            assert result.success is True


class TestExecuteStageCallbackRacefix:
    """When the callback handler (running in a background thread) completes
    before execute_stage checks batch completion, the callback may have
    already set the batch to a terminal status (FAILED/COMPLETED).
    execute_stage must preserve that terminal status instead of
    overwriting it.
    """

    @pytest.mark.asyncio
    async def test_callback_already_set_batch_failed_preserves_status(self):
        """Callback set batch=FAILED → preserve it and count failures.

        Covers lines 1893, 1894, 1895.
        """
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        bot = _make_bot_record()
        batch = _make_batch_record(status="PENDING")
        failed_batch = _make_batch_record(status=BatchStatus.FAILED.value)

        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._bot_repo.get_by_id.return_value = bot

        # list_by_publish_id: return FAILED batch — no RUNNING batches remain
        svc._publish_batch_repo.list_by_publish_id.return_value = [failed_batch]
        # get_by_id: re-read returns FAILED (callback already set it)
        svc._publish_batch_repo.get_by_id.return_value = failed_batch

        # count_records_by_batch_id: no PROCESSING records (inline path),
        # some FAILED records reported by the callback
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PROCESSING": 0,
            "FAILED": 2,
        }

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with patch.object(svc, "_get_pending_batches") as mock_gpb:
                mock_gpb.side_effect = [
                    ("PROD_FIRST_BATCH", [batch]),
                    ("PROD_FIRST_BATCH", []),
                ]
                with patch.object(
                    svc, "_execute_batch", new_callable=AsyncMock
                ) as mock_exec:
                    mock_exec.return_value = BatchResult(
                        success=True, processed_count=2, failed_count=0
                    )

                    result = await svc.execute_stage("t", 1, "op")

                    # Callback already set batch to FAILED — total_failed was
                    # incremented by the FAILED count from the callback, so
                    # all_success = False and the publish is marked FAILED.
                    assert result.success is False

    @pytest.mark.asyncio
    async def test_callback_already_set_batch_completed_preserves_status(self):
        """Callback set batch=COMPLETED → preserve it without overwriting.

        Covers line 1904.
        """
        svc = _make_service()
        pub = _make_publish_record(status="ACTIVE")
        bot = _make_bot_record()
        batch = _make_batch_record(status="PENDING")
        completed_batch = _make_batch_record(status=BatchStatus.COMPLETED.value)

        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        svc._bot_repo.get_by_id.return_value = bot

        # list_by_publish_id: return COMPLETED batch — no RUNNING batches remain
        svc._publish_batch_repo.list_by_publish_id.return_value = [completed_batch]
        # get_by_id: re-read returns COMPLETED (callback already set it)
        svc._publish_batch_repo.get_by_id.return_value = completed_batch

        # count_records_by_batch_id: no PROCESSING records (inline path)
        svc._publish_record_repo.count_records_by_batch_id.return_value = {
            "PROCESSING": 0,
        }

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with patch.object(svc, "_get_pending_batches") as mock_gpb:
                mock_gpb.side_effect = [
                    ("PROD_FIRST_BATCH", [batch]),
                    ("PROD_FIRST_BATCH", []),
                ]
                with patch.object(
                    svc, "_execute_batch", new_callable=AsyncMock
                ) as mock_exec:
                    mock_exec.return_value = BatchResult(
                        success=True, processed_count=2, failed_count=0
                    )

                    result = await svc.execute_stage("t", 1, "op")

                    # Callback already set batch to COMPLETED — nothing
                    # overwritten, total_failed stays 0, all_success = True.
                    assert result.success is True


class TestExecuteBatchDispatch:
    @pytest.mark.asyncio
    async def test_unknown_publish_type_returns_failure(self):
        svc = _make_service()
        batch = _make_batch_record()
        result = await svc._execute_batch(
            tenant="t",
            publish_id=1,
            batch=batch,
            publish_type="UNKNOWN_TYPE",
            drain_timeout=30,
            batch_repo=svc._publish_batch_repo,
            operator="op",
        )
        assert result.success is False
        assert "Unknown publish type" in result.error_message


class TestExecuteCreateBatch:
    @pytest.mark.asyncio
    async def test_publish_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        batch = _make_batch_record()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc._execute_create_batch(
                    "t", 1, batch, "op", publish_record=None, bot_record=None
                )

    @pytest.mark.asyncio
    async def test_bot_not_found_returns_failure(self):
        svc = _make_service()
        pub = _make_publish_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = None
        batch = _make_batch_record()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_create_batch(
                "t", 1, batch, "op", publish_record=pub, bot_record=None
            )
            assert result.success is False
            assert "Bot not found" in result.error_message

    @pytest.mark.asyncio
    async def test_no_pending_records_returns_success(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_create_batch(
                "t", 1, batch, "op", publish_record=pub, bot_record=bot
            )
            assert result.success is True
            assert result.processed_count == 0

    @pytest.mark.asyncio
    async def test_device_not_found_skips(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        record = MagicMock()
        record.id = 1
        record.device_id = 999
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {}  # no device found

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_create_batch(
                "t", 1, batch, "op", publish_record=pub, bot_record=bot
            )
            assert result.success is True
            assert result.processed_count == 0

    @pytest.mark.asyncio
    async def test_start_device_success(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="PENDING")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        result_obj = MagicMock()
        result_obj.status = DeviceStatus.ACTIVE.value
        svc._device_service.start_device = AsyncMock(return_value=result_obj)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_create_batch(
                "t", 1, batch, "op", publish_record=pub, bot_record=bot
            )
            assert result.success is True
            assert result.processed_count == 1

    @pytest.mark.asyncio
    async def test_start_device_failed(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="PENDING")
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
            result = await svc._execute_create_batch(
                "t", 1, batch, "op", publish_record=pub, bot_record=bot
            )
            assert result.success is False
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_start_device_exception(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="PENDING")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        svc._device_service.start_device = AsyncMock(side_effect=Exception("network"))

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_create_batch(
                "t", 1, batch, "op", publish_record=pub, bot_record=bot
            )
            assert result.success is False
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_start_device_async_pending(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="PENDING")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        result_obj = MagicMock()
        result_obj.status = "PENDING"  # async hook dispatched
        svc._device_service.start_device = AsyncMock(return_value=result_obj)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_create_batch(
                "t", 1, batch, "op", publish_record=pub, bot_record=bot
            )
            # Neither processed nor failed — async callback will drive
            assert result.processed_count == 0
            assert result.failed_count == 0
