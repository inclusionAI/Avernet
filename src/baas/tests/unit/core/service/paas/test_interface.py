"""Tests for PaasService ABC interface."""

import inspect

import pytest

from secbaas.community.api.device_manage import CommandResult
from secbaas.community.core.service.paas import (
    ArcaPaasService,
    ErrorCode,
    K8sPaasService,
    LocalPaasService,
    PaasError,
    PaasService,
    PoolabPaasService,
    SigmaPaasService,
    StandalonePaasService,
    TeClawPaasService,
)


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
            "FILE_TRANSFER_FAILED",
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


ALL_PLATFORM_SUBCLASSES = [
    ArcaPaasService,
    LocalPaasService,
    SigmaPaasService,
    PoolabPaasService,
    TeClawPaasService,
    K8sPaasService,
    StandalonePaasService,
]


class TestFileTransferConformance:
    """Verify all 7 platform PaasService subclasses have pull/push methods."""

    @pytest.mark.parametrize("service_cls", ALL_PLATFORM_SUBCLASSES)
    def test_all_subclasses_have_pull_file_from_url(self, service_cls):
        """Every platform subclass must have pull_file_from_url async method."""
        method = getattr(service_cls, "pull_file_from_url", None)
        assert method is not None, (
            f"{service_cls.__name__} does not have pull_file_from_url"
        )
        assert inspect.iscoroutinefunction(method), (
            f"{service_cls.__name__}.pull_file_from_url is not async"
        )
        sig = inspect.signature(method)
        param_names = list(sig.parameters.keys())
        assert "paas_device_id" in param_names, (
            f"{service_cls.__name__}.pull_file_from_url missing paas_device_id param"
        )
        assert "source_url" in param_names, (
            f"{service_cls.__name__}.pull_file_from_url missing source_url param"
        )

    @pytest.mark.parametrize("service_cls", ALL_PLATFORM_SUBCLASSES)
    def test_all_subclasses_have_push_file_to_url(self, service_cls):
        """Every platform subclass must have push_file_to_url async method."""
        method = getattr(service_cls, "push_file_to_url", None)
        assert method is not None, (
            f"{service_cls.__name__} does not have push_file_to_url"
        )
        assert inspect.iscoroutinefunction(method), (
            f"{service_cls.__name__}.push_file_to_url is not async"
        )
        sig = inspect.signature(method)
        param_names = list(sig.parameters.keys())
        assert "paas_device_id" in param_names, (
            f"{service_cls.__name__}.push_file_to_url missing paas_device_id param"
        )
        assert "device_path" in param_names, (
            f"{service_cls.__name__}.push_file_to_url missing device_path param"
        )
