"""
System config management domain types.

Package mirrors the bot_manager/ pattern:
- _models.py: Pydantic models
- _exceptions.py: Domain exceptions
- _protocols.py: Service protocol
"""

from ._exceptions import SystemConfigNotFoundError
from ._models import (
    SystemConfigCreate,
    SystemConfigListResponse,
    SystemConfigResponse,
    SystemConfigUpdate,
)
from ._protocols import SystemConfigManageService

__all__ = [
    "SystemConfigCreate",
    "SystemConfigListResponse",
    "SystemConfigManageService",
    "SystemConfigNotFoundError",
    "SystemConfigResponse",
    "SystemConfigUpdate",
]
