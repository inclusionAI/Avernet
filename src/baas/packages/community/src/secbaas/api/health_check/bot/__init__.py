"""
Bot health check public API types.

Re-exports all types consumers need. The private modules (_enums, _exceptions,
_models, _protocols) contain the actual definitions; this __init__.py is the
public face of the package.
"""

from ._enums import DeviceProviderType
from ._exceptions import (
    BotHealthCheckerError,
    HealthCheckError,
    HealthCheckTimeoutError,
    PartialSuccessError,
    SandboxNotFoundError,
    TTLExtendFailedError,
    UnsupportedDeviceProviderError,
)
from ._models import (
    AliveDeviceInfo,
    BotAliveCheckResult,
    BotDeviceInfo,
    BotDeviceListResponse,
    BotHealthCheckerConfig,
    BotHealthCheckResult,
    DeviceAliveStatus,
    FailedDeviceInfo,
    PaasDeviceInfo,
    PaasDeviceListResponse,
    TTLExtendResult,
    TTLInfo,
    resolve_alive_check_strategy,
    resolve_health_check_strategy,
)
from ._protocols import BotHealthCheckerService, DeviceSourceProvider

__all__ = [
    # Enums
    "DeviceProviderType",
    # Exceptions
    "BotHealthCheckerError",
    "HealthCheckError",
    "HealthCheckTimeoutError",
    "PartialSuccessError",
    "SandboxNotFoundError",
    "TTLExtendFailedError",
    "UnsupportedDeviceProviderError",
    # Models
    "AliveDeviceInfo",
    "BotAliveCheckResult",
    "DeviceAliveStatus",
    "BotDeviceInfo",
    "BotDeviceListResponse",
    "BotHealthCheckResult",
    "BotHealthCheckerConfig",
    "FailedDeviceInfo",
    "PaasDeviceInfo",
    "PaasDeviceListResponse",
    "TTLInfo",
    "TTLExtendResult",
    "resolve_alive_check_strategy",
    "resolve_health_check_strategy",
    # Protocols
    "BotHealthCheckerService",
    "DeviceSourceProvider",
]
