"""Unit tests for DeviceCallbackRequest and serialize_hook_result."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from secbaas.community.api.publish_manage import (
    DeviceCallbackRequest,
    serialize_hook_result,
)


class TestDeviceCallbackRequest:
    def test_valid_start_callback(self) -> None:
        req = DeviceCallbackRequest(
            device_uuid="DEVICE-abc123",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            exit_code=0,
            stdout="ok",
            stderr="",
            tenant="test_tenant",
        )
        assert req.device_uuid == "DEVICE-abc123"
        assert req.publish_id == 1
        assert req.event_type == "start"
        assert req.result_status == "SUCCESS"

    def test_valid_stop_callback(self) -> None:
        req = DeviceCallbackRequest(
            device_uuid="DEVICE-abc123",
            publish_id=2,
            event_type="stop",
            result_status="FAILED",
            tenant="test_tenant",
        )
        assert req.event_type == "stop"
        assert req.result_status == "FAILED"

    def test_lowercase_result_status(self) -> None:
        req = DeviceCallbackRequest(
            device_uuid="DEVICE-abc123",
            publish_id=3,
            event_type="start",
            result_status="success",
            tenant="test_tenant",
        )
        assert req.result_status == "success"

    def test_result_status_failed_case_insensitive(self) -> None:
        req = DeviceCallbackRequest(
            device_uuid="DEVICE-abc123",
            publish_id=4,
            event_type="start",
            result_status="failed",
            tenant="test_tenant",
        )
        assert req.result_status == "failed"

    def test_missing_device_uuid_fails(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCallbackRequest(  # type: ignore[call-arg]
                publish_id=1,
                event_type="start",
                result_status="SUCCESS",
                tenant="test_tenant",
            )

    def test_missing_publish_id_fails(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCallbackRequest(  # type: ignore[call-arg]
                device_uuid="DEVICE-abc",
                event_type="start",
                result_status="SUCCESS",
            )

    def test_zero_publish_id_fails(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCallbackRequest(
                device_uuid="DEVICE-abc",
                publish_id=0,
                event_type="start",
                result_status="SUCCESS",
                tenant="test_tenant",
            )

    def test_negative_publish_id_fails(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCallbackRequest(
                device_uuid="DEVICE-abc",
                publish_id=-1,
                event_type="start",
                result_status="SUCCESS",
                tenant="test_tenant",
            )

    def test_invalid_event_type_fails(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCallbackRequest(
                device_uuid="DEVICE-abc",
                publish_id=1,
                event_type="invalid",  # type: ignore[arg-type]
                result_status="SUCCESS",
                tenant="test_tenant",
            )

    def test_optional_fields_default_none(self) -> None:
        req = DeviceCallbackRequest(
            device_uuid="DEVICE-abc",
            publish_id=1,
            event_type="start",
            result_status="SUCCESS",
            tenant="test_tenant",
        )
        assert req.exit_code is None
        assert req.stdout is None
        assert req.stderr is None

    def test_missing_tenant_fails(self) -> None:
        with pytest.raises(ValidationError):
            DeviceCallbackRequest(  # type: ignore[call-arg]
                device_uuid="DEVICE-abc",
                publish_id=1,
                event_type="start",
                result_status="SUCCESS",
            )


class TestSerializeHookResult:
    def test_basic_success(self) -> None:
        result = serialize_hook_result(
            exit_code=0, stdout="ok", stderr="", message="Hook succeeded"
        )
        parsed = json.loads(result)
        assert parsed["exit_code"] == 0
        assert parsed["stdout"] == "ok"
        assert parsed["stderr"] == ""
        assert parsed["message"] == "Hook succeeded"

    def test_basic_failure(self) -> None:
        result = serialize_hook_result(
            exit_code=1,
            stdout="",
            stderr="error occurred",
            message="Hook failed",
        )
        parsed = json.loads(result)
        assert parsed["exit_code"] == 1
        assert parsed["stderr"] == "error occurred"

    def test_none_fields(self) -> None:
        result = serialize_hook_result(exit_code=None, stdout=None, stderr=None)
        parsed = json.loads(result)
        assert parsed["exit_code"] is None
        assert parsed["stdout"] is None
        assert parsed["stderr"] is None
        assert parsed["message"] == ""

    def test_no_args(self) -> None:
        result = serialize_hook_result()
        parsed = json.loads(result)
        assert parsed["exit_code"] is None
        assert parsed["message"] == ""

    def test_result_is_valid_json(self) -> None:
        result = serialize_hook_result(
            exit_code=0,
            stdout='some output with "quotes" and \n newlines',
            stderr="",
            message="ok",
        )
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_truncation_stderr(self) -> None:
        long_stderr = "x" * 2000
        result = serialize_hook_result(
            exit_code=1, stderr=long_stderr, message="failed"
        )
        parsed = json.loads(result)
        assert parsed["stderr"].endswith("[truncated]")
        assert len(result) <= 4096

    def test_truncation_stdout(self) -> None:
        long_stdout = "y" * 5000
        result = serialize_hook_result(exit_code=0, stdout=long_stdout, message="ok")
        parsed = json.loads(result)
        assert parsed["stdout"].endswith("[truncated]")
        assert len(result) <= 4096

    def test_truncation_both(self) -> None:
        long_stdout = "a" * 5000
        long_stderr = "b" * 2000
        result = serialize_hook_result(
            exit_code=1, stdout=long_stdout, stderr=long_stderr, message="fail"
        )
        parsed = json.loads(result)
        assert parsed["stdout"].endswith("[truncated]")
        assert parsed["stderr"].endswith("[truncated]")
        assert len(result) <= 4096

    def test_exact_budget_no_truncation(self) -> None:
        # Build a result that fits exactly within budget
        result = serialize_hook_result(
            exit_code=0, stdout="short", stderr="err", message="ok"
        )
        parsed = json.loads(result)
        assert parsed["stdout"] == "short"
        assert parsed["stderr"] == "err"
        assert "[truncated]" not in parsed["stdout"]
        assert "[truncated]" not in parsed["stderr"]

    def test_message_included(self) -> None:
        result = serialize_hook_result(message="Custom message")
        parsed = json.loads(result)
        assert parsed["message"] == "Custom message"

    def test_unicode_content(self) -> None:
        result = serialize_hook_result(
            exit_code=0, stdout="日本語テスト", stderr="中文测试", message="ok"
        )
        parsed = json.loads(result)
        assert parsed["stdout"] == "日本語テスト"
        assert parsed["stderr"] == "中文测试"

    def test_empty_strings(self) -> None:
        result = serialize_hook_result(exit_code=0, stdout="", stderr="")
        parsed = json.loads(result)
        assert parsed["stdout"] == ""
        assert parsed["stderr"] == ""
