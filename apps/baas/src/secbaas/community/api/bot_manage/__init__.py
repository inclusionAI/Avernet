"""
Bot management domain types.

Package structure mirrors api/domain/device_manage/ pattern.
Sub-modules are private — import all types from this package.
"""

from ._enums import BotDeviceStatus, BotStatus, SlaGrade
from ._models import (
    BotClusterCreate,
    BotConfig,
    BotDeviceStatusResponse,
    BotListResponse,
    BotQuery,
    BotResponse,
    BotStartProgressResponse,
    CreateBotResponse,
    DestroyBotResponse,
    FetchStartProgressResult,
    RestartBotResponse,
    ScaleBotResponse,
    StopBotResponse,
    UpdateBotResponse,
    UpdateDevicesResponse,
)
from ._protocols import BotCrudService, BotManageService

__all__ = [
    "BotClusterCreate",
    "BotConfig",
    "BotCrudService",
    "BotDeviceStatus",
    "BotDeviceStatusResponse",
    "BotListResponse",
    "BotManageService",
    "BotQuery",
    "BotResponse",
    "BotStartProgressResponse",
    "BotStatus",
    "CreateBotResponse",
    "DestroyBotResponse",
    "FetchStartProgressResult",
    "RestartBotResponse",
    "ScaleBotResponse",
    "SlaGrade",
    "StopBotResponse",
    "UpdateBotResponse",
    "UpdateDevicesResponse",
]
