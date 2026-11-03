"""Stable BaaS Service error contract shared by implementation and consumers."""

from __future__ import annotations


class BaasServiceError(Exception):
    """Base error for BaaS Service failures."""


class BaasBotNotFoundError(BaasServiceError):
    """BaaS confirms that the target Bot no longer exists."""


class BaasNoActiveDevicesError(BaasServiceError):
    """BaaS confirms that the Bot currently has no active device."""

    error_code = "NO_ACTIVE_DEVICES"

    def __init__(self, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(
            f"BaaS device is offline: status={status_code} "
            f"error={self.error_code}"
        )


class BaasPublishConflictError(BaasServiceError):
    """BaaS rejected the operation because another publish operation is active."""

    error_code = "PUBLISH_CONFLICT"

    def __init__(self, *, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"BaaS publish conflict: status={status_code} {message}")


class BaasTransientServiceError(BaasServiceError):
    """BaaS transport or 5xx failure that may succeed on a later attempt."""


__all__ = [
    "BaasBotNotFoundError",
    "BaasNoActiveDevicesError",
    "BaasPublishConflictError",
    "BaasServiceError",
    "BaasTransientServiceError",
]
