"""Tests for InstanceRouter exceptions.

Validates exception hierarchy and error message formatting.
"""

from secbaas.community.core.service.paas.desktop.instance_router._exceptions import (
    ForwardHTTPError,
    ForwardTimeoutError,
    InstanceNotFoundError,
    InstanceRouterError,
)


class TestInstanceRouterError:
    """Tests for the base InstanceRouterError."""

    def test_base_error_inheritance(self) -> None:
        """Test InstanceRouterError inherits from Exception."""
        error = InstanceRouterError("test error")

        assert isinstance(error, Exception)
        assert str(error) == "test error"


class TestInstanceNotFoundError:
    """Tests for InstanceNotFoundError."""

    def test_error_message(self) -> None:
        """Test error message format."""
        error = InstanceNotFoundError("machine-123", "dev")

        expected = "No instance found for machine machine-123 in env dev"
        assert str(error) == expected

    def test_attributes(self) -> None:
        """Test error stores machine_id and env."""
        error = InstanceNotFoundError("machine-456", "prod")

        assert error.machine_id == "machine-456"
        assert error.env == "prod"

    def test_inheritance(self) -> None:
        """Test InstanceNotFoundError inherits from InstanceRouterError."""
        error = InstanceNotFoundError("machine-123", "dev")

        assert isinstance(error, InstanceRouterError)


class TestForwardTimeoutError:
    """Tests for ForwardTimeoutError."""

    def test_error_message(self) -> None:
        """Test error message format."""
        error = ForwardTimeoutError("instance-b", "execute_command", 30.0)

        expected = (
            "Forward to instance-b timed out after 30.0s for action execute_command"
        )
        assert str(error) == expected

    def test_attributes(self) -> None:
        """Test error stores all attributes."""
        error = ForwardTimeoutError("instance-c", "create_device", 60.0)

        assert error.target_instance == "instance-c"
        assert error.action == "create_device"
        assert error.timeout == 60.0

    def test_inheritance(self) -> None:
        """Test ForwardTimeoutError inherits from InstanceRouterError."""
        error = ForwardTimeoutError("instance-b", "execute_command", 30.0)

        assert isinstance(error, InstanceRouterError)


class TestForwardHTTPError:
    """Tests for ForwardHTTPError."""

    def test_error_message(self) -> None:
        """Test error message format."""
        error = ForwardHTTPError("instance-b", 503, "Service Unavailable")

        expected = "Forward to instance-b failed with status 503"
        assert str(error) == expected

    def test_attributes(self) -> None:
        """Test error stores all attributes."""
        error = ForwardHTTPError("instance-c", 404, "Not Found")

        assert error.target_instance == "instance-c"
        assert error.status_code == 404
        assert error.response_body == "Not Found"

    def test_inheritance(self) -> None:
        """Test ForwardHTTPError inherits from InstanceRouterError."""
        error = ForwardHTTPError("instance-b", 500, "Internal Error")

        assert isinstance(error, InstanceRouterError)

    def test_status_code_zero(self) -> None:
        """Test status_code 0 indicates connection error."""
        error = ForwardHTTPError("instance-b", 0, "Connection refused")

        assert error.status_code == 0
        assert "status 0" in str(error)


class TestExceptionCatching:
    """Tests that exceptions can be caught via hierarchy."""

    def test_catch_all_instance_router_errors(self) -> None:
        """Test that all errors can be caught as InstanceRouterError."""
        errors = [
            InstanceNotFoundError("m1", "dev"),
            ForwardTimeoutError("i1", "action", 30.0),
            ForwardHTTPError("i2", 503, "error"),
        ]

        for error in errors:
            try:
                raise error
            except InstanceRouterError as caught:
                assert caught is error
