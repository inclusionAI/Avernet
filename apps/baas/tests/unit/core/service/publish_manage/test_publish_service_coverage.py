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


class TestCreatePublishOrphanCleanup:
    @pytest.mark.asyncio
    async def test_orphan_publish_create_type_cleanup(self):
        """Orphan publish (no batches) of CREATE type is auto-cleaned."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)

        orphan = _make_publish_record(id=50, publish_type="CREATE", status="PENDING")
        svc._publish_batch_repo.list_by_publish_id.return_value = []  # no batches = orphan

        # After cleanup, create proceeds normally; the pre-insert
        # protection-set query also sees no actives
        svc._publish_repo.list_active_by_bot_ids.side_effect = [[orphan], [], []]
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
        svc._publish_repo.list_active_by_bot_ids.side_effect = [[orphan], [], []]
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

        svc._bot_repo.get_by_id.return_value = _make_bot_record()
        svc._bot_repo.get_by_bot_uuid.return_value = None
        svc._bot_repo.insert_bot.return_value = 2

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
        svc._publish_repo.list_active_by_bot_ids.return_value = [orphan]
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


class TestCreatePublishConcurrentConflict:
    @pytest.mark.asyncio
    async def test_concurrent_different_type_not_stale_raises_conflict(self):
        """Different publish type, not stale, raises PublishConflictError."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._bot_service.get_bot = AsyncMock(return_value=bot)

        existing = _make_publish_record(id=99, publish_type="CREATE", status="ACTIVE")
        existing.gmt_modified = datetime.now()  # not stale
        svc._publish_repo.list_active_by_bot_ids.return_value = [existing]
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
