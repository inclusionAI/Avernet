"""Classify only positively identified device-read unavailability."""

import httpx

from agentclaw.community.core.devices.services.device_context import (
    DeviceConnectionUnavailableError,
    DeviceOfflineError,
)
from agentclaw.community.core.service_bot.baas_service_errors import (
    BaasNoActiveDevicesError,
    BaasTransientServiceError,
)


def raise_if_unavailable(error: Exception) -> None:
    if isinstance(error, BaasNoActiveDevicesError):
        raise DeviceOfflineError("Device is offline") from error
    if isinstance(error, (BaasTransientServiceError, httpx.RequestError)):
        raise DeviceConnectionUnavailableError("Device connection unavailable") from error
