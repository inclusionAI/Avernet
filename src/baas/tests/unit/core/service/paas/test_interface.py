"""Tests for PaasService ABC interface."""

import pytest

from secbaas.community.api.device_manage import CommandResult
from secbaas.community.core.service.paas import ErrorCode, PaasError, PaasService


class TestPaasServiceABC:
    """Test that PaasService ABC cannot be instantiated directly."""

    def test_cannot_instantiate_abstract_class(self):
        """PaasService is abstract and cannot be instantiated."""
        with pytest.raises(TypeError) as exc_info:
            PaasService()  # type: ignore[abstract]

        assert "abstract" in str(exc_info.value).lower()


class TestErrorCode:
    """Test ErrorCode enum values."""

    def test_error_code_values_are_strings(self):
        """All error code values should be strings for serialization."""
        for code in ErrorCode:
            assert isinstance(code.value, str)

    def test_required_error_codes_exist(self):
        """All expected error codes should be defined."""
        required_codes = [
            "DEVICE_NOT_FOUND",
            "DEVICE_ALREADY_EXISTS",
            "DEVICE_CREATION_FAILED",
            "DEVICE_DESTROY_FAILED",
            "COMMAND_TIMEOUT",
            "COMMAND_FAILED",
            "DEVICE_UNAVAILABLE",
            "PLATFORM_UNAVAILABLE",
            "AUTH_FAILED",
            "RATE_LIMITED",
        ]
        for code_name in required_codes:
            assert hasattr(ErrorCode, code_name)
            assert getattr(ErrorCode, code_name).value == code_name


class TestPaasError:
    """Test PaasError exception."""

    def test_error_message_format(self):
        """Error message should include code in brackets."""
        error = PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device xyz not found")
        assert "[DEVICE_NOT_FOUND]" in str(error)
        assert "Device xyz not found" in str(error)

    def test_error_stores_code_and_message(self):
        """Error should store code and message as attributes."""
        original = Exception("original error")
        error = PaasError(
            ErrorCode.DEVICE_CREATION_FAILED, "Creation failed", platform_error=original
        )
        assert error.code == ErrorCode.DEVICE_CREATION_FAILED
        assert error.message == "Creation failed"
        assert error.platform_error is original


class TestCommandResult:
    """Test CommandResult dataclass."""

    def test_command_result_creation(self):
        """CommandResult can be created with all fields."""
        result = CommandResult(
            exit_code=0,
            stdout="hello",
            stderr="",
            execution_time_ms=100,
            command="echo hello",
        )
        assert result.exit_code == 0
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.execution_time_ms == 100
        assert result.command == "echo hello"
