"""Stable BaaS Service error contract shared by implementation and consumers."""

from __future__ import annotations


class BaasServiceError(Exception):
    """Base error for BaaS Service failures."""


class BaasNoActiveDevicesError(BaasServiceError):
    """BaaS confirms that the Bot currently has no active device."""

    error_code = "NO_ACTIVE_DEVICES"

    def __init__(self, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(
            f"BaaS device is offline: status={status_code} "
            f"error={self.error_code}"
        )


class BaasTransientServiceError(BaasServiceError):
    """BaaS transport or 5xx failure that may succeed on a later attempt."""


__all__ = [
    "BaasNoActiveDevicesError",
    "BaasServiceError",
    "BaasTransientServiceError",
]
