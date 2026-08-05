"""PaaS adapter error codes and exception hierarchy.

Unified error code translation layer for Device lifecycle operations
across different PaaS platforms (Arca, Sigma, etc.).

Per Decision D-03: Platform-specific errors are translated to unified
codes at the implementation layer before raising.
"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Unified error codes for PaaS adapter operations.

    Device lifecycle errors:
        DEVICE_NOT_FOUND: Device/container does not exist
        DEVICE_ALREADY_EXISTS: Device with same identifier already exists
        DEVICE_CREATION_FAILED: Failed to create device/container
        DEVICE_DESTROY_FAILED: Failed to destroy device/container
        DEVICE_NOT_READY: Device/container did not become ready within timeout
        DEVICE_NOT_ACTIVE: Device exists but is not in ACTIVE status

    Command execution errors:
        COMMAND_TIMEOUT: Command execution exceeded timeout
        COMMAND_FAILED: Command execution failed (non-zero exit)
        DEVICE_UNAVAILABLE: Device exists but is not reachable/responding

    Platform connectivity errors:
        PLATFORM_UNAVAILABLE: PaaS platform API unreachable
        PLATFORM_ERROR: Generic platform error
        AUTH_FAILED: Authentication/authorization failed
        RATE_LIMITED: Rate limit exceeded

    Configuration errors:
        CONFIG_INVALID: Invalid or missing configuration

    Template errors:
        TEMPLATE_NOT_FOUND: Device template not found

    File transfer errors:
        FILE_TRANSFER_NOT_FOUND: File transfer not found
        FILE_TRANSFER_STATE_CONFLICT: File transfer state conflict
        FILE_TRANSFER_FAILED: File transfer operation failed (download/upload)
    """

    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    DEVICE_ALREADY_EXISTS = "DEVICE_ALREADY_EXISTS"
    DEVICE_CREATION_FAILED = "DEVICE_CREATION_FAILED"
    DEVICE_DESTROY_FAILED = "DEVICE_DESTROY_FAILED"
    DEVICE_NOT_READY = "DEVICE_NOT_READY"
    DEVICE_NOT_ACTIVE = "DEVICE_NOT_ACTIVE"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"
    PLATFORM_ERROR = "PLATFORM_ERROR"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    CONFIG_INVALID = "CONFIG_INVALID"
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
    NOT_FOUND = "NOT_FOUND"

    # Relay session errors (Phase 65)
    RELAY_SESSION_NOT_FOUND = "RELAY_SESSION_NOT_FOUND"
    RELAY_STATE_CONFLICT = "RELAY_STATE_CONFLICT"
    INVALID_STATUS = "INVALID_STATUS"

    # File transfer errors (Phase 67)
    FILE_TRANSFER_NOT_FOUND = "FILE_TRANSFER_NOT_FOUND"
    FILE_TRANSFER_STATE_CONFLICT = "FILE_TRANSFER_STATE_CONFLICT"
    FILE_TRANSFER_FAILED = "FILE_TRANSFER_FAILED"


class PaasError(Exception):
    """Base exception for PaaS adapter layer.

    Wraps platform-specific errors with unified error codes for
    consistent error handling across different PaaS platforms.

    Attributes:
        code: Unified error code from ErrorCode enum
        message: Human-readable error message
        platform_error: Original platform-specific exception (if any)
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        platform_error: Exception | None = None,
    ):
        self.code = code
        self.message = message
        self.platform_error = platform_error
        super().__init__(f"[{code.value}] {message}")

    def __repr__(self) -> str:
        return (
            f"PaasError(code={self.code.value!r}, "
            f"message={self.message!r}, "
            f"platform_error={self.platform_error!r})"
        )


class DeviceFacadeException(Exception):  # noqa: N818
    """Exception for PaaS Service Facade layer.

    Wraps PaasError with additional context for problem diagnosis.
    Provides user-friendly error messages for facade operations.

    Per Decision D-06: Facade layer wraps PaasError with context including
    template_id, operation, and paas_device_id for easier debugging.

    Attributes:
        operation: The facade operation being performed (create_device, destroy_device, execute_command)
        platform_type: The platform type ("ARCA", "Sigma", etc.)
        template_id: The device template ID involved in the operation
        paas_device_id: The device ID (with @template_id suffix when applicable)
        original_error: The original PaasError that was caught
        message: Human-readable error message with context
    """

    def __init__(
        self,
        operation: str,
        platform_type: str,
        template_id: int,
        paas_device_id: str | None,
        original_error: PaasError,
    ):
        self.operation = operation
        self.platform_type = platform_type
        self.template_id = template_id
        self.paas_device_id = paas_device_id
        self.original_error = original_error

        # Build user-friendly message with context
        device_info = f"device={paas_device_id}" if paas_device_id else "device=N/A"
        self.message = (
            f"Facade operation failed: {operation} "
            f"[{platform_type}, template_id={template_id}, {device_info}]: "
            f"[{original_error.code.value}] {original_error.message}"
        )

        super().__init__(self.message)

    def __repr__(self) -> str:
        return (
            f"DeviceFacadeException("
            f"operation={self.operation!r}, "
            f"platform_type={self.platform_type!r}, "
            f"template_id={self.template_id}, "
            f"paas_device_id={self.paas_device_id!r}, "
            f"original_error={self.original_error!r}"
            f")"
        )


class DeviceNotFoundException(DeviceFacadeException):
    """Raised when a device is not found.

    This exception is used specifically for cases where a device lookup
    fails because the device does not exist in the system.
    """

    def __init__(
        self,
        message: str,
        paas_device_id: str | None = None,
    ):
        # Create a dummy PaasError for the parent constructor
        dummy_error = PaasError(
            ErrorCode.DEVICE_NOT_FOUND,
            message,
        )
        super().__init__(
            operation="device_lookup",
            platform_type="UNKNOWN",
            template_id=0,
            paas_device_id=paas_device_id,
            original_error=dummy_error,
        )


class DeviceNotActiveException(DeviceFacadeException):
    """Raised when a device exists but is not in ACTIVE status.

    This exception is used when attempting to resolve connection info
    for a device that exists but cannot accept connections due to
    its current status (e.g., PENDING, STOPPED, ERROR).
    """

    def __init__(
        self,
        message: str,
        paas_device_id: str | None = None,
        device_status: str | None = None,
    ):
        # Create a dummy PaasError for the parent constructor
        dummy_error = PaasError(
            ErrorCode.DEVICE_NOT_ACTIVE,
            message,
        )
        super().__init__(
            operation="resolve_ws_conn_info",
            platform_type="UNKNOWN",
            template_id=0,
            paas_device_id=paas_device_id,
            original_error=dummy_error,
        )
        self.device_status = device_status


class DeviceCreationError(Exception):
    """Exception for device creation failures on local platform.

    Provides typed error handling with string error codes for specific
    failure scenarios during local Docker container creation.

    Per Decision D-DC04: Error codes are simple strings for extensibility.
    No automatic retry logic is implemented at this layer.

    Attributes:
        error_code: String error code for typed handling
        message: Human-readable error message
        context: Optional diagnostic context dict for detailed error information

    Common error codes:
        CONTAINER_LIMIT_EXCEEDED: Machine has reached max containers
        IMAGE_NOT_FOUND: Required Docker image not available
        RESOURCE_EXHAUSTED: Insufficient CPU/memory on target machine
        CREATION_FAILED: Generic container creation failure
        MACHINE_NOT_FOUND: Machine never registered in database
        MACHINE_OFFLINE: Machine registered but not connected
        WORKER_OFFLINE: Target worker process is offline or UDS connection failed (cross-process routing)
    """

    def __init__(
        self, error_code: str, message: str, context: dict[str, Any] | None = None
    ):
        self.error_code = error_code
        self.message = message
        self.context = context
        super().__init__(f"[{error_code}] {message}")

    def __repr__(self) -> str:
        return f"DeviceCreationError(error_code={self.error_code!r}, message={self.message!r}, context={self.context!r})"


DEVICE_CREATION_ERROR_TO_HTTP_STATUS: dict[str, int] = {
    "BAD_GATEWAY": 502,
    "COMMAND_FAILED": 500,
    "CONTAINER_LIMIT_EXCEEDED": 503,
    "CONTAINER_NOT_FOUND": 404,
    "CREATION_FAILED": 500,
    "DESTROY_FAILED": 500,
    "IMAGE_NOT_FOUND": 404,
    "INSTANCE_NOT_ASSIGNED": 503,
    "INVALID_DEVICE_TYPE": 400,
    "INVALID_PARAMS": 400,
    "INVALID_RESPONSE": 500,
    "INVALID_STATUS": 400,  # Phase 65
    "LAZY_ROUTER_INIT_FAILED": 500,
    "LOCAL_TEMPLATE_NOT_CONFIGURED": 500,
    "MACHINE_NOT_CONNECTED": 503,
    "MACHINE_NOT_FOUND": 404,
    "MACHINE_OFFLINE": 503,
    "MISSING_CONTAINER_ID": 400,
    "OPEN_FOLDER_FAILED": 500,
    "PATH_NOT_FOUND": 404,  # reserved for mng daemon dynamic error responses
    "PERMISSION_DENIED": 403,
    "QUERY_FAILED": 500,
    "RELAY_SESSION_NOT_FOUND": 404,  # Phase 65
    "RELAY_SETUP_FAILED": 502,
    "RELAY_STATE_CONFLICT": 409,  # Phase 65
    "RELAY_TIMEOUT": 502,
    # File transfer errors (Phase 67)
    "FILE_TRANSFER_NOT_FOUND": 404,
    "FILE_TRANSFER_STATE_CONFLICT": 409,
    "FILE_TRANSFER_FAILED": 502,
    "RESTART_FAILED": 500,
    "RESOURCE_EXHAUSTED": 503,
    "TIMEOUT": 502,
    "WORKER_OFFLINE": 503,
}
