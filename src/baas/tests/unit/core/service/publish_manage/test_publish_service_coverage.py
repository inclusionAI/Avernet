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


class TestExtraConfigToPublishConfig:
    def test_returns_none_for_none(self):
        assert _extra_config_to_publish_config(None) is None

    def test_returns_none_for_empty_dict(self):
        assert _extra_config_to_publish_config({}) is None

    def test_returns_publish_config_for_valid_dict(self):
        cfg = _extra_config_to_publish_config({"replica_desired": 3})
        assert isinstance(cfg, PublishConfig)
        assert cfg.replica_desired == 3


# ====================================================================
# _can_transition / _get_next_status
# ====================================================================


class TestStateTransitionHelpers:
    def test_can_transition_valid(self):
        svc = _make_service()
        assert svc._can_transition("PENDING", "approve") is True

    def test_can_transition_invalid(self):
        svc = _make_service()
        assert svc._can_transition("SUCCESS", "approve") is False

    def test_get_next_status_valid(self):
        svc = _make_service()
        assert svc._get_next_status("PENDING", "approve") == "ACTIVE"

    def test_get_next_status_invalid_returns_none(self):
        svc = _make_service()
        assert svc._get_next_status("SUCCESS", "approve") is None


# ====================================================================
# _cleanup_pending_clone_on_update_failure
# ====================================================================


class TestCleanupPendingCloneOnUpdateFailure:
    def test_publish_not_found_returns_early(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._cleanup_pending_clone_on_update_failure(
            tenant="t", publish_id=1, operator="op"
        )
        # bot_repo.get_by_id should never be called
        svc._bot_repo.get_by_id.assert_not_called()

    def test_no_target_bot_id_returns_early(self):
        svc = _make_service()
        pub = _make_publish_record(extra_config={})
        svc._publish_repo.get_by_id.return_value = pub
        svc._cleanup_pending_clone_on_update_failure(
            tenant="t", publish_id=1, operator="op"
        )
        svc._bot_repo.get_by_id.assert_not_called()

    def test_target_bot_not_found_returns_early(self):
        svc = _make_service()
        pub = _make_publish_record(extra_config={"target_bot_id": 99})
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = None
        svc._cleanup_pending_clone_on_update_failure(
            tenant="t", publish_id=1, operator="op"
        )
        svc._rel_repo.soft_delete_by_bot_id.assert_not_called()

    def test_target_bot_not_pending_returns_early(self):
        svc = _make_service()
        pub = _make_publish_record(extra_config={"target_bot_id": 99})
        svc._publish_repo.get_by_id.return_value = pub
        bot = _make_bot_record(status=BotStatus.ACTIVE.value)
        svc._bot_repo.get_by_id.return_value = bot
        svc._cleanup_pending_clone_on_update_failure(
            tenant="t", publish_id=1, operator="op"
        )
        svc._rel_repo.soft_delete_by_bot_id.assert_not_called()

    def test_rel_soft_delete_failure_does_not_block_bot_delete(self):
        svc = _make_service()
        pub = _make_publish_record(extra_config={"target_bot_id": 99})
        svc._publish_repo.get_by_id.return_value = pub
        bot = _make_bot_record(status=BotStatus.PENDING.value)
        svc._bot_repo.get_by_id.return_value = bot
        svc._rel_repo.soft_delete_by_bot_id.side_effect = Exception("db error")

        svc._cleanup_pending_clone_on_update_failure(
            tenant="t", publish_id=1, operator="op"
        )
        # bot_repo.soft_delete should still be called despite rel error
        svc._bot_repo.soft_delete.assert_called_once()

    def test_full_cleanup_success(self):
        svc = _make_service()
        pub = _make_publish_record(extra_config={"target_bot_id": 99})
        svc._publish_repo.get_by_id.return_value = pub
        bot = _make_bot_record(status=BotStatus.PENDING.value)
        svc._bot_repo.get_by_id.return_value = bot

        svc._cleanup_pending_clone_on_update_failure(
            tenant="t", publish_id=1, operator="op"
        )
        svc._rel_repo.soft_delete_by_bot_id.assert_called_once()
        svc._bot_repo.soft_delete.assert_called_once()


# ====================================================================
# create_publish — orphan publish handling
# ====================================================================


class TestCreatePublishOrphanCleanup:
    @pytest.mark.asyncio
    async def test_orphan_publish_create_type_cleanup(self):
        """Orphan publish (no batches) of CREATE type is auto-cleaned."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)

        orphan = _make_publish_record(id=50, publish_type="CREATE", status="PENDING")
        svc._publish_repo.get_active_by_bot_id.return_value = orphan
        svc._publish_batch_repo.list_by_publish_id.return_value = []  # no batches = orphan

        # After cleanup, create proceeds normally
        svc._publish_repo.get_active_by_bot_id.side_effect = [orphan, None]
        svc._publish_repo.insert_publish.return_value = 1

        new_pub = _make_publish_record(id=1, publish_type="CREATE", status="PENDING")
        svc._publish_repo.get_by_id.return_value = new_pub
        svc._publish_repo.now.return_value = datetime.now()

        mock_batch = _make_batch_record()
        svc._publish_batch_repo.insert_batch.return_value = 1
        svc._publish_batch_repo.get_by_id.return_value = mock_batch

        mock_device = _make_device(status="PENDING")
        svc._device_repo.list_by_bot_id.return_value = [mock_device]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.CREATE,
                operator="user1",
                request_id="req-id-12345678901234567890",
            )

        assert result.publish_type == "CREATE"
        # Verify orphan was marked FAILED
        svc._publish_repo.update_status.assert_called()

    @pytest.mark.asyncio
    async def test_orphan_publish_update_type_cleanup_calls_cleanup_clone(self):
        """Orphan UPDATE publish triggers _cleanup_pending_clone_on_update_failure."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)

        orphan = _make_publish_record(id=50, publish_type="UPDATE", status="PENDING")
        svc._publish_repo.get_active_by_bot_id.side_effect = [orphan, None]
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        # cleanup returns None (no target_bot_id)
        svc._publish_repo.get_by_id.return_value = orphan
        svc._publish_repo.insert_publish.return_value = 1
        svc._publish_repo.now.return_value = datetime.now()

        new_pub = _make_publish_record(id=1, publish_type="UPDATE", status="PENDING")
        # Make get_by_id return the orphan first (for cleanup), then the new pub
        svc._publish_repo.get_by_id.side_effect = [None, new_pub]

        mock_batch = _make_batch_record()
        svc._publish_batch_repo.insert_batch.return_value = 1
        svc._publish_batch_repo.get_by_id.return_value = mock_batch

        mock_device = _make_device(status="ACTIVE")
        svc._device_repo.list_by_bot_id.return_value = [mock_device]

        svc._bot_service.create_bot_record = AsyncMock(
            return_value=_make_bot_record(id=2)
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc.create_publish(
                tenant="test_tenant",
                bot_id=1,
                publish_type=PublishType.UPDATE,
                operator="user1",
                request_id="req-id-12345678901234567890",
                config=PublishConfig(replica_desired=3),
            )

        assert result.publish_type == "UPDATE"

    @pytest.mark.asyncio
    async def test_orphan_cleanup_failure_raises_conflict(self):
        """If orphan cleanup fails, PublishConflictError is raised."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)

        orphan = _make_publish_record(id=50, publish_type="CREATE", status="PENDING")
        svc._publish_repo.get_active_by_bot_id.return_value = orphan
        svc._publish_batch_repo.list_by_publish_id.return_value = []

        # Make update_status fail during cleanup
        svc._publish_repo.update_status.side_effect = Exception("db error")

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishConflictError, match="auto-cleanup failed"):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.CREATE,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                )


# ====================================================================
# create_publish — concurrent publish type mismatch
# ====================================================================


class TestCreatePublishConcurrentConflict:
    @pytest.mark.asyncio
    async def test_concurrent_different_type_not_stale_raises_conflict(self):
        """Different publish type, not stale, raises PublishConflictError."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)

        existing = _make_publish_record(id=99, publish_type="CREATE", status="ACTIVE")
        existing.gmt_modified = datetime.now()  # not stale
        svc._publish_repo.get_active_by_bot_id.return_value = existing
        svc._publish_repo.now.return_value = datetime.now()

        mock_batch = _make_batch_record(status="COMPLETED")
        svc._publish_batch_repo.list_by_publish_id.return_value = [mock_batch]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishConflictError, match="active CREATE publish"):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.UPDATE,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                )


# ====================================================================
# create_publish — bot not found
# ====================================================================


class TestCreatePublishBotNotFound:
    @pytest.mark.asyncio
    async def test_bot_not_found_raises_bot_not_found(self):
        from secbaas.community.api.bot_runtime import BotNotFoundError

        svc = _make_service()
        svc._bot_service.get_bot = AsyncMock(return_value=None)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(BotNotFoundError):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=999,
                    publish_type=PublishType.CREATE,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                )


# ====================================================================
# create_publish — SCALE_UP/DOWN validation errors
# ====================================================================


class TestCreatePublishScaleValidation:
    @pytest.mark.asyncio
    async def test_scale_up_target_equal_current_raises_error(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)
        svc._publish_repo.get_active_by_bot_id.return_value = None
        svc._publish_repo.now.return_value = datetime.now()

        mock_device = _make_device(status="ACTIVE")
        svc._device_repo.list_by_bot_id.return_value = [mock_device] * 3

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="Invalid scale operation"):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.SCALE_UP,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                    config=PublishConfig(replica_desired=3),
                )

    @pytest.mark.asyncio
    async def test_scale_down_target_equal_current_raises_error(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)
        svc._publish_repo.get_active_by_bot_id.return_value = None
        svc._publish_repo.now.return_value = datetime.now()

        mock_device = _make_device(status="ACTIVE")
        svc._device_repo.list_by_bot_id.return_value = [mock_device] * 3

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="Invalid scale operation"):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.SCALE_DOWN,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                    config=PublishConfig(replica_desired=3),
                )

    @pytest.mark.asyncio
    async def test_scale_up_zero_delta_raises_error(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)
        svc._publish_repo.get_active_by_bot_id.return_value = None
        svc._publish_repo.now.return_value = datetime.now()

        mock_device = _make_device(status="ACTIVE")
        svc._device_repo.list_by_bot_id.return_value = [mock_device] * 5

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="no devices to scale"):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.SCALE_UP,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                    config=PublishConfig(replica_desired=5),
                )


# ====================================================================
# create_publish — UPDATE without replica_desired
# ====================================================================


class TestCreatePublishUpdateNoReplica:
    @pytest.mark.asyncio
    async def test_update_no_replica_desired_raises_error(self):
        svc = _make_service()
        bot = _make_bot_record(replica_desired=None)
        svc._bot_service.get_bot = AsyncMock(return_value=bot)
        svc._publish_repo.get_active_by_bot_id.return_value = None
        svc._publish_repo.now.return_value = datetime.now()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="no replica_desired"):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.UPDATE,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                )


# ====================================================================
# create_publish — RESTART with no eligible devices
# ====================================================================


class TestCreatePublishRestartNoEligible:
    @pytest.mark.asyncio
    async def test_restart_no_eligible_devices_raises_error(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)
        svc._publish_repo.get_active_by_bot_id.return_value = None
        svc._publish_repo.now.return_value = datetime.now()

        # All devices RELEASED - not eligible for restart
        mock_device = _make_device(status="RELEASED")
        svc._device_repo.list_by_bot_id.return_value = [mock_device]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(
                ValueError, match="No eligible devices found to restart"
            ):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.RESTART,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_restart_unhealthy_scope_no_eligible_raises(self):
        svc = _make_service()
        bot = _make_bot_record(config=None)
        svc._bot_service.get_bot = AsyncMock(return_value=bot)
        svc._publish_repo.get_active_by_bot_id.return_value = None
        svc._publish_repo.now.return_value = datetime.now()

        # Only ACTIVE devices, but scope is UNHEALTHY
        mock_device = _make_device(status="ACTIVE")
        svc._device_repo.list_by_bot_id.return_value = [mock_device]

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(
                ValueError, match="No eligible devices found to restart"
            ):
                await svc.create_publish(
                    tenant="test_tenant",
                    bot_id=1,
                    publish_type=PublishType.RESTART,
                    operator="user1",
                    request_id="req-id-12345678901234567890",
                    config=PublishConfig(
                        restart_scope=RestartScope.UNHEALTHY,
                    ),
                )


# ====================================================================
# _generate_batches — STOP, UPDATE_DEVICE, DESTROY
# ====================================================================


class TestGenerateBatchesAdditionalTypes:
    def test_generate_batches_stop_type(self):
        svc = _make_service()
        config = PublishConfig(replica_desired=3, batch_capacity=5)
        batches = svc._generate_batches(PublishType.STOP, config)
        assert len(batches) >= 1
        assert all(b.stage == PublishStage.PROD_FIRST_BATCH.value for b in batches)

    def test_generate_batches_update_device_type(self):
        svc = _make_service()
        config = PublishConfig(replica_desired=2, batch_capacity=5)
        batches = svc._generate_batches(PublishType.UPDATE_DEVICE, config)
        assert len(batches) >= 1
        assert all(b.stage == PublishStage.PROD_FIRST_BATCH.value for b in batches)

    def test_generate_batches_destroy_type(self):
        svc = _make_service()
        config = PublishConfig(replica_desired=4, batch_capacity=5)
        batches = svc._generate_batches(PublishType.DESTROY, config)
        assert len(batches) >= 1

    def test_generate_batches_scale_up_cooldown_zero(self):
        svc = _make_service()
        config = PublishConfig(replica_desired=3, batch_capacity=5)
        batches = svc._generate_batches(PublishType.SCALE_UP, config)
        for b in batches:
            assert b.cooldown_seconds == 0

    def test_generate_batches_batch_capacity_override(self):
        svc = _make_service()
        config = PublishConfig(replica_desired=12, batch_capacity=3)
        batches = svc._generate_batches(PublishType.CREATE, config)
        for b in batches:
            assert b.batch_capacity <= 3

    def test_generate_batches_no_config_uses_defaults(self):
        svc = _make_service()
        batches = svc._generate_batches(PublishType.CREATE, None)
        assert len(batches) >= 1

    def test_generate_batches_with_empty_stages_uses_defaults(self):
        svc = _make_service()
        config = PublishConfig(replica_desired=5, stages={})
        batches = svc._generate_batches(PublishType.CREATE, config)
        assert len(batches) >= 1


# ====================================================================
# _create_device_records_for_publish — all publish types
# ====================================================================


class TestCreateDeviceRecordsForPublish:
    def test_create_no_pending_devices_raises(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = []

        batch = _make_batch_record(batch_capacity=1)
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="No eligible devices"):
                svc._create_device_records_for_publish(
                    tenant="t",
                    env="test",
                    publish_id=1,
                    publish_type=PublishType.CREATE,
                    bot_record=bot,
                    batch_records=[batch],
                    operator="op",
                )

    def test_update_no_eligible_devices_raises(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [_make_device(status="RELEASED")]
        batch = _make_batch_record()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="No eligible devices"):
                svc._create_device_records_for_publish(
                    tenant="t",
                    env="test",
                    publish_id=1,
                    publish_type=PublishType.UPDATE,
                    bot_record=bot,
                    batch_records=[batch],
                    operator="op",
                )

    def test_restart_with_unhealthy_scope(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, status="FAILED"),
            _make_device(id=2, status="ACTIVE"),  # not eligible for UNHEALTHY
        ]
        pub = _make_publish_record(
            extra_config={"restart_scope": "unhealthy", "replica_desired": 1}
        )
        svc._publish_repo.get_by_id.return_value = pub
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.RESTART,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )
        svc._publish_record_repo.insert_record.assert_called_once()

    def test_scale_up_no_template_uuid_returns_early(self):
        svc = _make_service()
        bot = _make_bot_record(template_uuid=None)
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.SCALE_UP,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )
        assert result is None
        svc._publish_record_repo.insert_record.assert_not_called()

    def test_scale_up_template_not_found_returns_early(self):
        svc = _make_service()
        bot = _make_bot_record(template_uuid="tpl-missing")
        svc._template_service.get_online_template_by_uuid.return_value = None
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.SCALE_UP,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )
        assert result is None
        svc._publish_record_repo.insert_record.assert_not_called()

    def test_scale_up_creates_devices(self):
        svc = _make_service()
        bot = _make_bot_record()
        bot.config = MagicMock()
        bot.config.deploy_config = None
        template = MagicMock()
        svc._template_service.get_online_template_by_uuid.return_value = template

        new_device = _make_device(id=100, device_uuid="new-dev-1", status="PENDING")
        svc._device_service.create_device.return_value = new_device

        batch = _make_batch_record(batch_capacity=2)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.SCALE_UP,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )
        assert svc._device_service.create_device.call_count == 2
        assert svc._rel_repo.insert_rel.call_count == 2

    def test_scale_up_uses_publish_config_deploy_config(self):
        svc = _make_service()
        bot = _make_bot_record()
        bot.config = MagicMock()
        bot.config.deploy_config = MagicMock()
        template = MagicMock()
        svc._template_service.get_online_template_by_uuid.return_value = template

        new_device = _make_device(id=100, device_uuid="new-dev-1", status="PENDING")
        svc._device_service.create_device.return_value = new_device

        from secbaas.community.api.device_manage import DeployConfig

        publish_deploy_config = DeployConfig(docker_image="v2")
        publish_config = PublishConfig(
            replica_desired=5,
            deploy_config=publish_deploy_config,
        )
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.SCALE_UP,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
                publish_config=publish_config,
            )

        call_args = svc._device_service.create_device.call_args
        kwargs = call_args.kwargs
        device_create_data = kwargs["data"]

        assert device_create_data.extra_config.deploy_config == publish_deploy_config

    def test_scale_down_selects_active_devices(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, status="ACTIVE"),
            _make_device(id=2, status="RELEASED"),  # not eligible
        ]
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.SCALE_DOWN,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )
        svc._publish_record_repo.insert_record.assert_called_once()

    def test_destroy_selects_active_devices(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, status="ACTIVE"),
        ]
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.DESTROY,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )
        svc._publish_record_repo.insert_record.assert_called_once()

    def test_stop_selects_active_devices(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, status="ACTIVE"),
        ]
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.STOP,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )
        svc._publish_record_repo.insert_record.assert_called_once()

    def test_update_device_with_target_uuids(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, device_uuid="uuid-1", status="ACTIVE"),
            _make_device(id=2, device_uuid="uuid-2", status="ACTIVE"),
        ]
        pub = _make_publish_record(extra_config={"target_device_uuids": ["uuid-1"]})
        svc._publish_repo.get_by_id.return_value = pub
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.UPDATE_DEVICE,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )
        svc._publish_record_repo.insert_record.assert_called_once()

    def test_update_device_target_uuids_parse_failure(self):
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, device_uuid="uuid-1", status="ACTIVE"),
        ]
        # Invalid extra_config that fails model_validate
        pub = _make_publish_record(extra_config={"invalid": object()})
        svc._publish_repo.get_by_id.return_value = pub
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="No eligible devices"):
                svc._create_device_records_for_publish(
                    tenant="t",
                    env="test",
                    publish_id=1,
                    publish_type=PublishType.UPDATE_DEVICE,
                    bot_record=bot,
                    batch_records=[batch],
                    operator="op",
                )

    def test_insert_record_includes_provider_device_id_in_extra_config(self):
        """Verify extra_config captures device_uuid + provider_device_id on insert."""
        import dataclasses

        from secbaas.community.core.repository.publish_record import (
            PublishRecordExtraConfig,
        )

        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(
                id=1, device_uuid="uuid-abc", provider_device_id="provider-xyz"
            ),
        ]
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.CREATE,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )

        svc._publish_record_repo.insert_record.assert_called_once()
        call_kwargs = svc._publish_record_repo.insert_record.call_args.kwargs
        assert "extra_config" in call_kwargs
        assert call_kwargs["extra_config"] == {
            "device_uuid": "uuid-abc",
            "provider_device_id": "provider-xyz",
        }

    def test_insert_record_extra_config_with_provider_none(self):
        """Verify extra_config still captures device_uuid when provider_device_id is None."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, device_uuid="uuid-abc", provider_device_id=None),
        ]
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.CREATE,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )

        call_kwargs = svc._publish_record_repo.insert_record.call_args.kwargs
        assert call_kwargs["extra_config"] == {
            "device_uuid": "uuid-abc",
            "provider_device_id": None,
        }

    def test_insert_record_extra_config_both_none_is_omitted(self):
        """Verify extra_config is None when both fields are None."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, device_uuid=None, provider_device_id=None),
        ]
        batch = _make_batch_record(batch_capacity=1)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            svc._create_device_records_for_publish(
                tenant="t",
                env="test",
                publish_id=1,
                publish_type=PublishType.CREATE,
                bot_record=bot,
                batch_records=[batch],
                operator="op",
            )

        call_kwargs = svc._publish_record_repo.insert_record.call_args.kwargs
        assert call_kwargs["extra_config"] is None


# ====================================================================
# _get_current_stage / _get_pending_batches / _check_all_batches_complete
# ====================================================================


class TestStageHelpers:
    def test_get_current_stage_no_batches_returns_none(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            assert svc._get_current_stage("t", 1) is None

    def test_get_current_stage_all_completed_returns_success(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(status="COMPLETED"),
        ]
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            assert svc._get_current_stage("t", 1) == PublishStage.SUCCESS.value

    def test_get_current_stage_returns_first_non_completed(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(stage="PREPUB", status="COMPLETED"),
            _make_batch_record(stage="GRAY", status="PENDING"),
        ]
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            assert svc._get_current_stage("t", 1) == "GRAY"

    def test_get_pending_batches_no_batches(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            stage, batches = svc._get_pending_batches("t", 1)
            assert stage is None
            assert batches == []

    def test_get_pending_batches_all_complete(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(stage="PREPUB", status="COMPLETED"),
        ]
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            stage, batches = svc._get_pending_batches("t", 1)
            assert stage == PublishStage.SUCCESS.value
            assert batches == []

    def test_get_pending_batches_returns_pending(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(id=1, stage="PREPUB", status="COMPLETED"),
            _make_batch_record(id=2, stage="GRAY", status="PENDING"),
            _make_batch_record(id=3, stage="GRAY", status="PENDING"),
        ]
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            stage, batches = svc._get_pending_batches("t", 1)
            assert stage == "GRAY"
            assert len(batches) == 2

    def test_check_all_batches_complete_no_batches(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            assert svc._check_all_batches_complete("t", 1) is False

    def test_check_all_batches_complete_with_pending(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(status="COMPLETED"),
            _make_batch_record(status="PENDING"),
        ]
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            assert svc._check_all_batches_complete("t", 1) is False

    def test_check_all_batches_complete_all_done(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = [
            _make_batch_record(status="COMPLETED"),
            _make_batch_record(status="FAILED"),
        ]
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            assert svc._check_all_batches_complete("t", 1) is True


# ====================================================================
# _get_publish_and_bot_record / get_publish_bot_uuid
# ====================================================================


class TestGetPublishAndBotRecord:
    def test_publish_not_found(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            pub, bot = svc._get_publish_and_bot_record("t", 1)
            assert pub is None
            assert bot is None

    def test_bot_not_found(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = _make_publish_record()
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            pub, bot = svc._get_publish_and_bot_record("t", 1)
            assert pub is None
            assert bot is None

    def test_success(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id_including_deleted.return_value = bot
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result_pub, result_bot = svc._get_publish_and_bot_record("t", 1)
            assert result_pub is pub
            assert result_bot is bot


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


# ====================================================================
# get_publish
# ====================================================================


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


# ====================================================================
# _build_publish_response / _refresh_publish_response
# ====================================================================


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


# ====================================================================
# approve_stage — edge cases
# ====================================================================


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


# ====================================================================
# reject_publish / revoke_publish — invalid status
# ====================================================================


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


# ====================================================================
# list_publishes
# ====================================================================


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


# ====================================================================
# execute_stage — edge cases
# ====================================================================


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


# ====================================================================
# execute_stage — callback race condition fix (lines 1890-1907)
# ====================================================================


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


# ====================================================================
# _execute_batch — unknown type dispatch
# ====================================================================


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


# ====================================================================
# _execute_create_batch
# ====================================================================


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


# ====================================================================
# _execute_update_batch
# ====================================================================


class TestExecuteUpdateBatch:
    @pytest.mark.asyncio
    async def test_no_target_bot_id_uses_current_bot(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record(extra_config={})  # no target_bot_id
        bot = _make_bot_record(extra_config={})
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}
        svc._device_repo.get_by_id.return_value = device

        drain_result = DrainResult(
            success=True, sessions_remaining=0, duration_seconds=0.0
        )
        with patch.object(
            svc, "_drain_device", new_callable=AsyncMock, return_value=drain_result
        ):
            update_result = MagicMock()
            update_result.status = DeviceStatus.ACTIVE.value
            svc._device_service.update_device = AsyncMock(return_value=update_result)

            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_update_batch(
                    "t",
                    1,
                    batch,
                    30,
                    "op",
                    publish_record=pub,
                    bot_record=bot,
                )
                assert result.success is True
                assert result.processed_count == 1

    @pytest.mark.asyncio
    async def test_update_device_failed_status(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record(extra_config={"target_bot_id": 2})
        bot = _make_bot_record()
        target_bot = _make_bot_record(id=2, extra_config={})
        svc._bot_repo.get_by_id.return_value = target_bot

        batch = _make_batch_record()
        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        drain_result = DrainResult(
            success=True, sessions_remaining=0, duration_seconds=0.0
        )
        with patch.object(
            svc, "_drain_device", new_callable=AsyncMock, return_value=drain_result
        ):
            update_result = MagicMock()
            update_result.status = DeviceStatus.FAILED.value
            update_result.err_msg = "update error"
            svc._device_service.update_device = AsyncMock(return_value=update_result)

            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_update_batch(
                    "t",
                    1,
                    batch,
                    30,
                    "op",
                    publish_record=pub,
                    bot_record=bot,
                )
                assert result.success is False
                assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_update_device_exception(self):
        svc = _make_service()
        pub = _make_publish_record(extra_config={"target_bot_id": 2})
        bot = _make_bot_record()
        target_bot = _make_bot_record(id=2, extra_config={})
        svc._bot_repo.get_by_id.return_value = target_bot

        batch = _make_batch_record()
        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            svc._device_service.update_device = AsyncMock(
                side_effect=Exception("connection error")
            )
            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_update_batch(
                    "t",
                    1,
                    batch,
                    30,
                    "op",
                    publish_record=pub,
                    bot_record=bot,
                )
                assert result.success is False
                assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_update_no_pending_records(self):
        svc = _make_service()
        pub = _make_publish_record()
        bot = _make_bot_record()
        batch = _make_batch_record()
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_update_batch(
                "t",
                1,
                batch,
                30,
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is True
            assert result.processed_count == 0

    @pytest.mark.asyncio
    async def test_update_bot_not_found(self):
        svc = _make_service()
        pub = _make_publish_record()
        svc._publish_repo.get_by_id.return_value = pub
        svc._bot_repo.get_by_id.return_value = None
        batch = _make_batch_record()

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_update_batch(
                "t",
                1,
                batch,
                30,
                "op",
                publish_record=pub,
                bot_record=None,
            )
            assert result.success is False

    @pytest.mark.asyncio
    async def test_update_device_not_found_skips(self):
        svc = _make_service()
        pub = _make_publish_record(extra_config={"target_bot_id": 2})
        bot = _make_bot_record()
        target_bot = _make_bot_record(id=2)
        svc._bot_repo.get_by_id.return_value = target_bot
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
            result = await svc._execute_update_batch(
                "t",
                1,
                batch,
                30,
                "op",
                publish_record=pub,
                bot_record=bot,
            )
            assert result.success is True
            assert result.processed_count == 0


# ====================================================================
# _execute_restart_batch
# ====================================================================


class TestExecuteRestartBatch:
    @pytest.mark.asyncio
    async def test_no_pending_records(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_restart_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is False
            assert "No pending records" in result.error_message

    @pytest.mark.asyncio
    async def test_restart_success(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()
        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            restart_result = MagicMock()
            restart_result.status = DeviceStatus.ACTIVE.value
            svc._device_service.restart_device = AsyncMock(return_value=restart_result)

            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_restart_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is True
                assert result.processed_count == 1

    @pytest.mark.asyncio
    async def test_restart_failed_status(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()
        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            restart_result = MagicMock()
            restart_result.status = DeviceStatus.FAILED.value
            restart_result.err_msg = "restart error"
            svc._device_service.restart_device = AsyncMock(return_value=restart_result)

            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_restart_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is False
                assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_restart_exception(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()
        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            svc._device_service.restart_device = AsyncMock(
                side_effect=Exception("timeout")
            )
            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_restart_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is False
                assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_restart_device_not_found_skips(self):
        svc = _make_service()
        pub = _make_publish_record()
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
            result = await svc._execute_restart_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is True
            assert result.processed_count == 0

    @pytest.mark.asyncio
    async def test_restart_async_status(self):
        from secbaas.community.api.device_manage import DeviceStatus

        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()
        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            restart_result = MagicMock()
            restart_result.status = "UPDATING"  # async
            svc._device_service.restart_device = AsyncMock(return_value=restart_result)

            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_restart_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.processed_count == 0
                assert result.failed_count == 0


# ====================================================================
# _execute_scale_batch
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


# ====================================================================
# _execute_destroy_batch
# ====================================================================


class TestExecuteDestroyBatch:
    @pytest.mark.asyncio
    async def test_no_pending_records(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_destroy_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is True
            assert result.processed_count == 0

    @pytest.mark.asyncio
    async def test_destroy_success(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE", device_uuid="uuid-10")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
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
                result = await svc._execute_destroy_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is True
                assert result.processed_count == 1

    @pytest.mark.asyncio
    async def test_destroy_failure(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            destroy_resp = MagicMock()
            destroy_resp.success = False
            destroy_resp.error_message = "destroy fail"
            destroy_resp.hook_result = None
            svc._device_service.destroy_device_by_uuid = AsyncMock(
                return_value=destroy_resp
            )

            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_destroy_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is False
                assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_destroy_exception(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            svc._device_service.destroy_device_by_uuid = AsyncMock(
                side_effect=Exception("timeout")
            )
            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_destroy_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is False
                assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_destroy_device_not_found_skips(self):
        svc = _make_service()
        pub = _make_publish_record()
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
            result = await svc._execute_destroy_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is True
            assert result.processed_count == 0

    @pytest.mark.asyncio
    async def test_destroy_success_with_warning(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE", device_uuid="uuid-10")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            destroy_resp = MagicMock()
            destroy_resp.success = True
            destroy_resp.error_message = "warning msg"
            destroy_resp.hook_result = None
            svc._device_service.destroy_device_by_uuid = AsyncMock(
                return_value=destroy_resp
            )

            svc._rel_repo.list_by_bot_id.return_value = []

            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_destroy_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is True

    @pytest.mark.asyncio
    async def test_destroy_success_with_hook_result(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE", device_uuid="uuid-10")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
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
                result = await svc._execute_destroy_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is True

    @pytest.mark.asyncio
    async def test_destroy_rel_cleanup(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE", device_uuid="uuid-10")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
            destroy_resp = MagicMock()
            destroy_resp.success = True
            destroy_resp.error_message = None
            destroy_resp.hook_result = None
            svc._device_service.destroy_device_by_uuid = AsyncMock(
                return_value=destroy_resp
            )

            rel = MagicMock()
            rel.id = 200
            rel.device_uuid = "uuid-10"
            svc._rel_repo.list_by_bot_id.return_value = [rel]

            with patch(
                "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
                return_value="test",
            ):
                result = await svc._execute_destroy_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is True
                svc._rel_repo.soft_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_destroy_failure_with_hook_result(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=True, sessions_remaining=0, duration_seconds=0.0
            ),
        ):
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
                result = await svc._execute_destroy_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is False

    @pytest.mark.asyncio
    async def test_destroy_drain_timeout(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE", device_uuid="uuid-10")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        with patch.object(
            svc,
            "_drain_device",
            new_callable=AsyncMock,
            return_value=DrainResult(
                success=False,
                sessions_remaining=5,
                duration_seconds=30.0,
                timeout_reached=True,
            ),
        ):
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
                result = await svc._execute_destroy_batch(
                    "t", 1, batch, 30, "op", publish_record=pub
                )
                assert result.success is True


# ====================================================================
# _execute_stop_batch
# ====================================================================


class TestExecuteStopBatch:
    @pytest.mark.asyncio
    async def test_no_pending_records(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_stop_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is True
            assert result.processed_count == 0

    @pytest.mark.asyncio
    async def test_stop_success(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        stop_resp = MagicMock()
        stop_resp.success = True
        stop_resp.error_message = None
        stop_resp.hook_result = None
        svc._device_service.stop_device_by_uuid = AsyncMock(return_value=stop_resp)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_stop_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is True
            assert result.processed_count == 1

    @pytest.mark.asyncio
    async def test_stop_failure(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        stop_resp = MagicMock()
        stop_resp.success = False
        stop_resp.error_message = "stop fail"
        stop_resp.hook_result = None
        svc._device_service.stop_device_by_uuid = AsyncMock(return_value=stop_resp)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_stop_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is False
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_stop_exception(self):
        svc = _make_service()
        pub = _make_publish_record()
        batch = _make_batch_record()

        device = _make_device(id=10, status="ACTIVE")
        record = MagicMock()
        record.id = 1
        record.device_id = 10
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [record]
        svc._device_repo.get_by_ids.return_value = {10: device}

        svc._device_service.stop_device_by_uuid = AsyncMock(
            side_effect=Exception("err")
        )

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_stop_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is False
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_stop_success_with_hook_result(self):
        svc = _make_service()
        pub = _make_publish_record()
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
        stop_resp = MagicMock()
        stop_resp.success = True
        stop_resp.error_message = None
        stop_resp.hook_result = hook
        svc._device_service.stop_device_by_uuid = AsyncMock(return_value=stop_resp)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_stop_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is True

    @pytest.mark.asyncio
    async def test_stop_failure_with_hook_result(self):
        svc = _make_service()
        pub = _make_publish_record()
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
        stop_resp = MagicMock()
        stop_resp.success = False
        stop_resp.error_message = "fail"
        stop_resp.hook_result = hook
        svc._device_service.stop_device_by_uuid = AsyncMock(return_value=stop_resp)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            result = await svc._execute_stop_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is False

    @pytest.mark.asyncio
    async def test_stop_device_not_found_skips(self):
        svc = _make_service()
        pub = _make_publish_record()
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
            result = await svc._execute_stop_batch(
                "t", 1, batch, 30, "op", publish_record=pub
            )
            assert result.success is True
            assert result.processed_count == 0


# ====================================================================
# _drain_device
# ====================================================================


class TestDrainDevice:
    @pytest.mark.asyncio
    async def test_drain_mock_mode_skips(self):
        svc = _make_service()
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.is_paas_mock_mode",
            return_value=True,
        ):
            result = await svc._drain_device("t", 1, timeout_seconds=30)
            assert result.success is True
            assert result.sessions_remaining == 0

    @pytest.mark.asyncio
    async def test_drain_zero_sessions(self):
        from secbaas.community.core.service.health_check.paas import (
            ActiveSessionVerdict,
        )

        svc = _make_service()
        with (
            patch(
                "secbaas.community.core.service.publish_manage._publish_service.is_paas_mock_mode",
                return_value=False,
            ),
            patch.object(
                svc,
                "_get_active_sessions",
                new_callable=AsyncMock,
                return_value=ActiveSessionVerdict.CLEAR,
            ),
        ):
            result = await svc._drain_device("t", 1, timeout_seconds=30)
            assert result.success is True
            assert result.sessions_remaining == 0
            assert result.verdict == ActiveSessionVerdict.CLEAR.value

    @pytest.mark.asyncio
    async def test_drain_timeout(self):
        from secbaas.community.core.service.health_check.paas import (
            ActiveSessionVerdict,
        )

        svc = _make_service()
        # Always ACTIVE, timeout=0 should hit immediately and block drain.
        with (
            patch(
                "secbaas.community.core.service.publish_manage._publish_service.is_paas_mock_mode",
                return_value=False,
            ),
            patch.object(
                svc,
                "_get_active_sessions",
                new_callable=AsyncMock,
                return_value=ActiveSessionVerdict.ACTIVE,
            ),
        ):
            result = await svc._drain_device(
                "t",
                1,
                timeout_seconds=0,
                check_interval=0.01,
            )
            assert result.success is False
            assert result.sessions_remaining > 0
            assert result.timeout_reached is True
            assert result.verdict == ActiveSessionVerdict.ACTIVE.value


# ====================================================================
# handle_device_callback
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


# ====================================================================
# _check_batch_completion
# ====================================================================


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


# ====================================================================
# _check_stage_advancement
# ====================================================================


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


# ====================================================================
# complete_publish — various publish types
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


# ====================================================================
# _check_and_handle_timeout
# ====================================================================


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


# ====================================================================
# get_publish_progress
# ====================================================================


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


# ====================================================================
# _aggregate_stage_progress
# ====================================================================


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


# ====================================================================
# _compute_overall_progress
# ====================================================================


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


# ====================================================================
# _get_device_details
# ====================================================================


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


# ====================================================================
# _auto_execute_stages
# ====================================================================


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


# ====================================================================
# _should_auto_complete
# ====================================================================


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


# ====================================================================
# retry_publish — not found
# ====================================================================


class TestRetryPublishNotFound:
    @pytest.mark.asyncio
    async def test_retry_not_found_raises(self):
        svc = _make_service()
        svc._publish_repo.get_by_id.return_value = None
        svc._bot_repo.get_by_id_including_deleted.return_value = None
        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(PublishNotFoundError):
                await svc.retry_publish("t", 1, "op", "req-123")
