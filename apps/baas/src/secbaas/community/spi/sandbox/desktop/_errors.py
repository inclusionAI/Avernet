"""SPI layer sandbox plugin exception definitions.

Zero Domain dependencies — the Service layer is responsible for translating
SandboxPluginError into DeviceCreationError for upstream consumers.
"""

from __future__ import annotations

from enum import StrEnum


class SandboxPluginErrorCode(StrEnum):
    """Structured error codes for SandboxPluginError.

    These codes allow the Service layer to match on specific error conditions
    when translating plugin-level exceptions to domain-level exceptions.
    """

    RELAY_TIMEOUT = "RELAY_TIMEOUT"
    """Relay command timed out (no type:"result" response within 30s)."""

    MACHINE_OFFLINE = "MACHINE_OFFLINE"
    """Target machine mng daemon connection lost."""

    RELAY_SETUP_FAILED = "RELAY_SETUP_FAILED"
    """mng daemon returned status:"error" or relay setup failed."""


class SandboxPluginError(Exception):
    """Plugin-layer unified exception with structured error_code.

    The Service layer catches SandboxPluginError and translates it to a
    DeviceCreationError for upstream consumers:

        try:
            ...
        except SandboxPluginError as e:
            raise DeviceCreationError(
                error_code=e.error_code,
                message=str(e),
            )

    Attributes:
        error_code: Machine-readable error code (str or SandboxPluginErrorCode enum value).
    """

    def __init__(self, error_code: SandboxPluginErrorCode | str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)
