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
            "publish_max_retry_times": 0,
            "publish_retry_count": 0,
            "attempt_started_at": None,
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
            "publish_max_retry_times": 0,
            "publish_retry_count": 0,
            "attempt_started_at": None,
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

    def test_scale_down_with_target_device_uuids_filters_eligible(self):
        """SCALE_DOWN with target_device_uuids filters ACTIVE devices to the requested set."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, device_uuid="uuid-a", status="ACTIVE"),
            _make_device(id=2, device_uuid="uuid-b", status="ACTIVE"),
            _make_device(id=3, device_uuid="uuid-c", status="ACTIVE"),  # filtered out
        ]
        # target_device_uuids live on the persisted publish record's extra_config.
        pub = _make_publish_record(
            extra_config={"target_device_uuids": ["uuid-a", "uuid-b"]}
        )
        svc._publish_repo.get_by_id.return_value = pub
        batch = _make_batch_record(batch_capacity=2)

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

        assert svc._publish_record_repo.insert_record.call_count == 2
        inserted_device_ids = [
            call.kwargs["device_id"]
            for call in svc._publish_record_repo.insert_record.call_args_list
        ]
        assert sorted(inserted_device_ids) == [1, 2]
        assert 3 not in inserted_device_ids

    def test_scale_down_with_target_device_uuids_missing_uuid_raises(self):
        """SCALE_DOWN raises ValueError when a requested UUID is not ACTIVE/owned."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, device_uuid="uuid-a", status="ACTIVE"),
            _make_device(id=2, device_uuid="uuid-b", status="ACTIVE"),
        ]
        pub = _make_publish_record(
            extra_config={"target_device_uuids": ["uuid-a", "nonexistent"]}
        )
        svc._publish_repo.get_by_id.return_value = pub
        batch = _make_batch_record(batch_capacity=2)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="nonexistent"):
                svc._create_device_records_for_publish(
                    tenant="t",
                    env="test",
                    publish_id=1,
                    publish_type=PublishType.SCALE_DOWN,
                    bot_record=bot,
                    batch_records=[batch],
                    operator="op",
                )

    def test_scale_down_with_target_device_uuids_count_mismatch_raises(self):
        """SCALE_DOWN raises ValueError when target count != batch capacity (scale_amount)."""
        svc = _make_service()
        bot = _make_bot_record()
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=1, device_uuid="uuid-a", status="ACTIVE"),
            _make_device(id=2, device_uuid="uuid-b", status="ACTIVE"),
            _make_device(id=3, device_uuid="uuid-c", status="ACTIVE"),
        ]
        # scale_amount = batch_capacity = 2, but target_uuids has 3 entries.
        pub = _make_publish_record(
            extra_config={"target_device_uuids": ["uuid-a", "uuid-b", "uuid-c"]}
        )
        svc._publish_repo.get_by_id.return_value = pub
        batch = _make_batch_record(batch_capacity=2)

        with patch(
            "secbaas.community.core.service.publish_manage._publish_service.get_current_env",
            return_value="test",
        ):
            with pytest.raises(ValueError, match="scale_amount"):
                svc._create_device_records_for_publish(
                    tenant="t",
                    env="test",
                    publish_id=1,
                    publish_type=PublishType.SCALE_DOWN,
                    bot_record=bot,
                    batch_records=[batch],
                    operator="op",
                )

    def test_scale_down_without_target_device_uuids_oldest_active_behavior(self):
        """SCALE_DOWN without target_device_uuids preserves oldest-ACTIVE selection (by id)."""
        svc = _make_service()
        bot = _make_bot_record()
        # Devices deliberately out of id order to verify sort-by-id before slicing.
        svc._device_repo.list_by_bot_id.return_value = [
            _make_device(id=3, device_uuid="uuid-c", status="ACTIVE"),
            _make_device(id=1, device_uuid="uuid-a", status="ACTIVE"),
            _make_device(id=2, device_uuid="uuid-b", status="ACTIVE"),
        ]
        # No target_device_uuids on persisted record → old behavior (None becomes {}).
        pub = _make_publish_record(extra_config=None)
        svc._publish_repo.get_by_id.return_value = pub
        # Oldest 2 ACTIVE devices by id should be selected (ids 1 and 2).
        batch = _make_batch_record(batch_capacity=2)

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

        assert svc._publish_record_repo.insert_record.call_count == 2
        inserted_device_ids = [
            call.kwargs["device_id"]
            for call in svc._publish_record_repo.insert_record.call_args_list
        ]
        # Oldest ACTIVE devices (ids 1, 2) selected over id=3.
        assert sorted(inserted_device_ids) == [1, 2]
        assert 3 not in inserted_device_ids


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
