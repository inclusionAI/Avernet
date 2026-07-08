"""
Tenant management domain types.

Package mirrors the bot_manager/ pattern:
- _enums.py: Enumerations
- _models.py: Pydantic models
- _exceptions.py: Domain exceptions
- _protocols.py: Service protocol
"""

from ._enums import ImagePullPolicy, TenantType
from ._exceptions import TenantNotFoundError
from ._models import (
    TenantConfig,
    TenantCreate,
    TenantListResponse,
    TenantResponse,
    TenantUpdate,
)
from ._protocols import TenantManageService

__all__ = [
    "ImagePullPolicy",
    "TenantConfig",
    "TenantCreate",
    "TenantListResponse",
    "TenantManageService",
    "TenantNotFoundError",
    "TenantResponse",
    "TenantType",
    "TenantUpdate",
]
