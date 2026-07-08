"""Device services for the core devices module."""

from agentclaw.community.core.devices.errors import (
    DeviceAllocateError,
    DeviceExecShellError,
    DeviceNotFoundError,
    DeviceReleaseError,
    DeviceServiceError,
    InvalidDeviceStatusError,
)
from agentclaw.community.core.devices.services.baas_device_service import (
    BaasDeviceService,
    BaasDeviceServiceError,
)
from agentclaw.community.core.devices.services.device_service import (
    ARCA_DEVICE_PROVIDER,
    LOCAL_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.core.devices.services.local_device_service import (
    LocalDeviceAllocateError,
    LocalDeviceReleaseError,
    LocalDeviceService,
)


__all__ = [
    # Base class and constants
    "DeviceService",
    "LOCAL_DEVICE_PROVIDER",
    "ARCA_DEVICE_PROVIDER",
    # Exceptions
    "DeviceServiceError",
    "DeviceNotFoundError",
    "InvalidDeviceStatusError",
    "LocalDeviceAllocateError",
    "LocalDeviceReleaseError",
    "DeviceAllocateError",
    "DeviceReleaseError",
    "DeviceExecShellError",
    "BaasDeviceServiceError",
    # Service implementations
    "LocalDeviceService",
    "BaasDeviceService",
    # NOTE: ArcaDeviceService is corp-only (constructed solely in the corp
    # devices column). It is deliberately NOT re-exported here so the community
    # package surface names no ARCA service (B11 Phase A). Corp code imports it
    # directly from ``arca_device_service``.
]
