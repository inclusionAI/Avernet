"""
Device template management domain types.

Package mirrors the bot_manager/ pattern:
- _enums.py: Enumerations
- _models.py: Pydantic models
- _exceptions.py: Domain exceptions
- _protocols.py: Service protocol
"""

from ._enums import TemplateStatus
from ._exceptions import TemplateByIdNotFoundError, TemplateNotFoundError
from ._models import (
    ArcaTemplateConfig,
    DeviceTemplateConfig,
    DeviceTemplateConfigUnion,
    DeviceTemplateResponse,
    DockerTemplateConfig,
    K8sTemplateConfig,
    LocalTemplateConfig,
    PoolabTemplateConfig,
    SigmaTemplateConfig,
    TeClawTemplateConfig,
    TemplateCreate,
    TemplateListResponse,
    TemplateUpdate,
)
from ._protocols import DeviceTemplateManageService

__all__ = [
    "ArcaTemplateConfig",
    "DeviceTemplateConfig",
    "DeviceTemplateConfigUnion",
    "DeviceTemplateManageService",
    "DeviceTemplateResponse",
    "K8sTemplateConfig",
    "DockerTemplateConfig",
    "LocalTemplateConfig",
    "PoolabTemplateConfig",
    "SigmaTemplateConfig",
    "TeClawTemplateConfig",
    "TemplateByIdNotFoundError",
    "TemplateCreate",
    "TemplateListResponse",
    "TemplateNotFoundError",
    "TemplateStatus",
    "TemplateUpdate",
]
