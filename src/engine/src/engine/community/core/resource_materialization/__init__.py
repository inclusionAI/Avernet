"""Bot workspace file materialization."""

from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
    MaterializationResult,
)
from engine.community.core.resource_materialization.service import (
    ResourceMaterializationService,
)

__all__ = [
    "MaterializationRequest",
    "MaterializationResult",
    "ResourceMaterializationService",
]
