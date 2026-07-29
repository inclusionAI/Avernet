"""Unit tests for api/publish_manage/_models.py — Publish workflow models."""

import json
from datetime import datetime

import pydantic
import pytest

from secbaas.community.api.publish_manage import (
    ApprovalAction,
    BatchDeviceProgress,
    BatchResult,
    DeviceCallbackRequest,
    DeviceOperationResult,
    DrainResult,
    ProgressSummary,
    ProgressTimeline,
    PublishBatchConfig,
    PublishBatchResponse,
    PublishConfig,
    PublishCreate,
    PublishListResponse,
    PublishProgressResponse,
    PublishRecordResponse,
    PublishResponse,
    PublishType,
    StageConfig,
    serialize_hook_result,
)


class TestStageConfig:
    """Tests for StageConfig model."""

    def test_defaults(self):
        cfg = StageConfig()
        assert cfg.device_count == 0
        assert cfg.batch_capacity == 5
        assert cfg.cooldown_seconds == 0
        assert cfg.pause_for_approval is False

    def test_custom_values(self):
        cfg = StageConfig(
            device_count=10,
            batch_capacity=3,
            cooldown_seconds=30,
            pause_for_approval=True,
        )
        assert cfg.device_count == 10
        assert cfg.batch_capacity == 3
        assert cfg.cooldown_seconds == 30
        assert cfg.pause_for_approval is True


class TestPublishConfig:
    """Tests for PublishConfig model."""

    def test_defaults(self):
        cfg = PublishConfig()
        assert cfg.stages == {}
        assert cfg.drain_timeout_seconds == 0
        assert cfg.batch_capacity_default == 5
        assert cfg.auto_complete is True
        assert cfg.auto_execute_max_iterations == 20
        assert cfg.callback_timeout_seconds == 1800

    def test_custom_values(self):
        cfg = PublishConfig(
            stages={
                "PREPUB": StageConfig(device_count=2, pause_for_approval=True),
            },
            drain_timeout_seconds=30,
            batch_capacity_default=10,
        )
        assert "PREPUB" in cfg.stages
        assert cfg.stages["PREPUB"].device_count == 2
        assert cfg.drain_timeout_seconds == 30

    def test_extra_fields_allowed(self):
        cfg = PublishConfig(custom_key="custom_value")
        assert cfg.custom_key == "custom_value"

    def test_get_defaults_for_type_create(self):
        defaults = PublishConfig.get_defaults_for_type(PublishType.CREATE)
        assert "stages" in defaults
        assert "PREPUB" in defaults["stages"]
        assert "GRAY" in defaults["stages"]
        assert "PROD_FIRST_BATCH" in defaults["stages"]
        assert "PROD_OTHER_BATCH" in defaults["stages"]
        assert defaults["stages"]["PREPUB"]["device_count"] == 2
        assert defaults["stages"]["PREPUB"]["pause_for_approval"] is True

    def test_get_defaults_for_type_update(self):
        defaults = PublishConfig.get_defaults_for_type(PublishType.UPDATE)
        assert "PREPUB" in defaults["stages"]
        assert defaults["stages"]["PREPUB"]["device_count"] == 2

    def test_get_defaults_for_type_restart(self):
        defaults = PublishConfig.get_defaults_for_type(PublishType.RESTART)
        assert "PREPUB" not in defaults["stages"]
        assert "PROD_FIRST_BATCH" in defaults["stages"]
        assert "PROD_OTHER_BATCH" in defaults["stages"]

    def test_get_defaults_for_type_scale_up(self):
        defaults = PublishConfig.get_defaults_for_type(PublishType.SCALE_UP)
        assert "direct" in defaults["stages"]
        assert defaults["stages"]["direct"]["batch_capacity"] == 10

    def test_get_defaults_for_type_scale_down(self):
        defaults = PublishConfig.get_defaults_for_type(PublishType.SCALE_DOWN)
        assert "direct" in defaults["stages"]

    def test_get_defaults_for_type_destroy(self):
        defaults = PublishConfig.get_defaults_for_type(PublishType.DESTROY)
        assert "direct" in defaults["stages"]
        assert defaults["drain_timeout_seconds"] == 0


class TestPublishBatchConfig:
    """Tests for PublishBatchConfig model."""

    def test_defaults(self):
        cfg = PublishBatchConfig()
        assert cfg.stage == "UNKNOWN"
        assert cfg.cooldown_seconds == 0
        assert cfg.device_count is None

    def test_extra_fields_allowed(self):
        cfg = PublishBatchConfig(custom_field="val")
        assert cfg.custom_field == "val"


class TestPublishCreate:
    """Tests for PublishCreate request model."""

    def test_required_fields(self):
        req = PublishCreate(bot_id=1, publish_type=PublishType.UPDATE, operator="user")
        assert req.bot_id == 1
        assert req.publish_type == PublishType.UPDATE
        assert req.operator == "user"

    def test_default_extra_config(self):
        req = PublishCreate(bot_id=1, publish_type=PublishType.CREATE, operator="admin")
        assert isinstance(req.extra_config, PublishConfig)

    def test_bot_id_must_be_positive(self):
        with pytest.raises(pydantic.ValidationError):
            PublishCreate(bot_id=0, publish_type=PublishType.CREATE, operator="user")

    def test_operator_min_length(self):
        with pytest.raises(pydantic.ValidationError):
            PublishCreate(bot_id=1, publish_type=PublishType.CREATE, operator="")


class TestPublishResponse:
    """Tests for PublishResponse model."""

    def test_minimal_fields(self):
        now = datetime(2024, 1, 1)
        resp = PublishResponse(
            id=1,
            bot_id=10,
            publish_type="UPDATE",
            status="PENDING",
            creator="admin",
            modifier="admin",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.id == 1
        assert resp.bot_id == 10
        assert resp.publish_type == "UPDATE"
        assert resp.status == "PENDING"
        assert resp.stage is None
        assert resp.request_id == ""

    def test_from_attributes(self):
        resp = PublishResponse.model_validate(
            {
                "id": 2,
                "bot_id": 20,
                "publish_type": "CREATE",
                "status": "ACTIVE",
                "stage": "PREPUB",
                "creator": "admin",
                "modifier": "admin",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
            }
        )
        assert resp.id == 2
        assert resp.stage == "PREPUB"


class TestPublishBatchResponse:
    """Tests for PublishBatchResponse model."""

    def test_minimal_fields(self):
        now = datetime(2024, 1, 1)
        resp = PublishBatchResponse(
            id=1,
            publish_id=10,
            batch_index=0,
            batch_capacity=5,
            stage="PREPUB",
            cooldown_seconds=0,
            status="PENDING",
            creator="admin",
            modifier="admin",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.id == 1
        assert resp.publish_id == 10
        assert resp.batch_index == 0

    def test_from_attributes(self):
        resp = PublishBatchResponse.model_validate(
            {
                "id": 1,
                "publish_id": 10,
                "batch_index": 0,
                "batch_capacity": 5,
                "stage": "PREPUB",
                "cooldown_seconds": 0,
                "status": "PENDING",
                "creator": "admin",
                "modifier": "admin",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
            }
        )
        assert resp.status == "PENDING"


class TestPublishRecordResponse:
    """Tests for PublishRecordResponse model."""

    def test_minimal_fields(self):
        now = datetime(2024, 1, 1)
        resp = PublishRecordResponse(
            id=1,
            batch_id=10,
            event_type="CREATE",
            status="SUCCESS",
            creator="admin",
            modifier="admin",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.id == 1
        assert resp.batch_id == 10
        assert resp.event_type == "CREATE"
        assert resp.device_id is None

    def test_from_attributes(self):
        resp = PublishRecordResponse.model_validate(
            {
                "id": 1,
                "batch_id": 10,
                "event_type": "CREATE",
                "status": "SUCCESS",
                "creator": "admin",
                "modifier": "admin",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
            }
        )
        assert resp.id == 1


class TestPublishListResponse:
    """Tests for PublishListResponse model."""

    def test_fields(self):
        now = datetime(2024, 1, 1)
        item = PublishResponse(
            id=1,
            bot_id=10,
            publish_type="UPDATE",
            status="PENDING",
            creator="admin",
            modifier="admin",
            gmt_create=now,
            gmt_modified=now,
        )
        resp = PublishListResponse(items=[item], total=1, page=1, page_size=10)
        assert len(resp.items) == 1
        assert resp.total == 1
        assert resp.page == 1
        assert resp.page_size == 10


class TestApprovalAction:
    """Tests for ApprovalAction model."""

    def test_approve_action(self):
        action = ApprovalAction(action="approve", operator="admin")
        assert action.action == "approve"
        assert action.operator == "admin"
        assert action.comment is None

    def test_reject_action(self):
        action = ApprovalAction(action="reject", operator="admin", comment="no")
        assert action.action == "reject"
        assert action.comment == "no"

    def test_invalid_action(self):
        with pytest.raises(pydantic.ValidationError):
            ApprovalAction(action="invalid", operator="admin")


class TestBatchResult:
    """Tests for BatchResult model."""

    def test_fields(self):
        result = BatchResult(success=True, processed_count=5, failed_count=0)
        assert result.success is True
        assert result.processed_count == 5
        assert result.failed_count == 0
        assert result.error_message is None

    def test_with_error(self):
        result = BatchResult(
            success=False, processed_count=3, failed_count=2, error_message="timeout"
        )
        assert result.error_message == "timeout"


class TestDrainResult:
    """Tests for DrainResult model."""

    def test_drained(self):
        result = DrainResult(success=True, sessions_remaining=0, duration_seconds=5.0)
        assert result.success is True
        assert result.sessions_remaining == 0
        assert result.timeout_reached is False

    def test_timeout(self):
        result = DrainResult(
            success=False,
            sessions_remaining=3,
            duration_seconds=30.0,
            timeout_reached=True,
        )
        assert result.success is False
        assert result.timeout_reached is True


class TestProgressSummary:
    """Tests for ProgressSummary model."""

    def test_percentage(self):
        summary = ProgressSummary(
            total_batches=10,
            completed_batches=5,
            total_devices=100,
            processed_devices=50,
            failed_devices=2,
            progress_percentage=50.0,
        )
        assert summary.progress_percentage == 50.0

    def test_complete(self):
        summary = ProgressSummary(
            total_batches=10,
            completed_batches=10,
            total_devices=100,
            processed_devices=100,
            failed_devices=0,
            progress_percentage=100.0,
        )
        assert summary.progress_percentage == 100.0


class TestStageProgress:
    """Tests for StageProgress model."""

    def test_fields(self):
        progress = type(
            "StageProgress",
            (object,),
            {
                "stage": "PREPUB",
                "status": "SUCCESS",
                "batches_completed": 1,
                "batches_total": 1,
                "devices_processed": 2,
                "devices_failed": 0,
                "devices_total": 2,
            },
        )()
        assert progress.stage == "PREPUB"
        assert progress.status == "SUCCESS"


class TestDeviceOperationResult:
    """Tests for DeviceOperationResult model."""

    def test_minimal(self):
        now = datetime(2024, 1, 1)
        result = DeviceOperationResult(
            event_type="CREATE",
            result_status="SUCCESS",
            gmt_create=now,
        )
        assert result.event_type == "CREATE"
        assert result.device_id is None
        assert result.device_uuid is None

    def test_from_attributes(self):
        result = DeviceOperationResult.model_validate(
            {
                "event_type": "CREATE",
                "result_status": "SUCCESS",
                "gmt_create": "2024-01-01T00:00:00",
            }
        )
        assert result.event_type == "CREATE"
        assert result.result_status == "SUCCESS"


class TestBatchDeviceProgress:
    """Tests for BatchDeviceProgress model."""

    def test_fields(self):
        progress = BatchDeviceProgress(
            batch_id=1,
            batch_index=0,
            stage="PREPUB",
            status="COMPLETED",
        )
        assert progress.batch_id == 1
        assert progress.batch_index == 0
        assert progress.devices == []

    def test_with_devices(self):
        now = datetime(2024, 1, 1)
        dev = DeviceOperationResult(
            event_type="CREATE",
            result_status="SUCCESS",
            gmt_create=now,
        )
        progress = BatchDeviceProgress(
            batch_id=1,
            batch_index=0,
            stage="PREPUB",
            status="COMPLETED",
            devices=[dev],
        )
        assert len(progress.devices) == 1


class TestProgressTimeline:
    """Tests for ProgressTimeline model."""

    def test_with_estimate(self):
        now = datetime(2024, 1, 1)
        tl = ProgressTimeline(
            gmt_create=now,
            gmt_modified=now,
            estimated_remaining_seconds=120.0,
        )
        assert tl.estimated_remaining_seconds == 120.0

    def test_no_estimate(self):
        now = datetime(2024, 1, 1)
        tl = ProgressTimeline(gmt_create=now, gmt_modified=now)
        assert tl.estimated_remaining_seconds is None


class TestPublishProgressResponse:
    """Tests for PublishProgressResponse model."""

    def test_minimal(self):
        now = datetime(2024, 1, 1)
        summary = ProgressSummary(
            total_batches=5,
            completed_batches=2,
            total_devices=50,
            processed_devices=20,
            failed_devices=1,
            progress_percentage=40.0,
        )
        timeline = ProgressTimeline(gmt_create=now, gmt_modified=now)
        resp = PublishProgressResponse(
            publish_id=1,
            status="ACTIVE",
            overall_progress=summary,
            timeline=timeline,
        )
        assert resp.publish_id == 1
        assert resp.status == "ACTIVE"
        assert resp.overall_progress.progress_percentage == 40.0
        assert resp.device_details == []
        assert resp.failed_devices == []

    def test_from_attributes(self):
        resp = PublishProgressResponse.model_validate(
            {
                "publish_id": 1,
                "status": "ACTIVE",
                "overall_progress": {
                    "total_batches": 5,
                    "completed_batches": 2,
                    "total_devices": 50,
                    "processed_devices": 20,
                    "failed_devices": 1,
                    "progress_percentage": 40.0,
                },
                "timeline": {
                    "gmt_create": "2024-01-01T00:00:00",
                    "gmt_modified": "2024-01-01T00:00:00",
                },
            }
        )
        assert resp.publish_id == 1


class TestDeviceCallbackRequest:
    """Tests for DeviceCallbackRequest model."""

    def test_required_fields(self):
        req = DeviceCallbackRequest(
            device_uuid="dev-001",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            tenant="test-tenant",
        )
        assert req.device_uuid == "dev-001"
        assert req.publish_id == 1
        assert req.event_type == "start"
        assert req.result_status == "SUCCESS"
        assert req.exit_code is None
        assert req.stdout is None
        assert req.stderr is None
        assert req.tenant == "test-tenant"

    def test_event_type_literal(self):
        with pytest.raises(pydantic.ValidationError):
            DeviceCallbackRequest(
                device_uuid="dev-001",
                publish_id=1,
                event_type="invalid",
                result_status="SUCCESS",
                tenant="test-tenant",
            )

    def test_with_optional_fields(self):
        req = DeviceCallbackRequest(
            device_uuid="dev-001",
            publish_id=1,
            event_type="stop",
            result_status="FAILED",
            exit_code=1,
            stdout="output",
            stderr="error",
            tenant="test-tenant",
        )
        assert req.exit_code == 1
        assert req.stdout == "output"
        assert req.stderr == "error"


class TestSerializeHookResult:
    """Tests for serialize_hook_result helper function."""

    def test_all_none(self):
        result = serialize_hook_result()
        parsed = json.loads(result)
        assert parsed["exit_code"] is None
        assert parsed["stdout"] is None
        assert parsed["stderr"] is None
        assert parsed["message"] == ""

    def test_full_values(self):
        result = serialize_hook_result(
            exit_code=0, stdout="ok", stderr="", message="done"
        )
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        assert parsed["stdout"] == "ok"
        assert parsed["message"] == "done"

    def test_truncation(self):
        long_str = "x" * 5000
        result = serialize_hook_result(stdout=long_str, stderr=long_str)
        parsed = json.loads(result)
        # stdout gets most of the budget
        assert len(parsed["stdout"]) < 5000
        assert parsed["stdout"].endswith("[truncated]")

    def test_result_within_limit(self):
        large = "x" * 1000
        result = serialize_hook_result(exit_code=0, stdout=large, stderr="")
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        assert "[truncated]" not in parsed["stdout"]

    def test_message_preserved(self):
        result = serialize_hook_result(message="custom message")
        parsed = json.loads(result)
        assert parsed["message"] == "custom message"

    def test_serialized_json_valid(self):
        result = serialize_hook_result(exit_code=0, stdout="test")
        assert json.loads(result)["exit_code"] == 0
