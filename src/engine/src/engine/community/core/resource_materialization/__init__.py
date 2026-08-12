"""Bot workspace file materialization."""

from engine.community.core.resource_materialization.models import (
    ChatAttachmentMaterializationRequest,
    MaterializationRequest,
    MaterializationResult,
)
from engine.community.core.resource_materialization.service import (
    ResourceMaterializationService,
)

__all__ = [
    "MaterializationRequest",
    "ChatAttachmentMaterializationRequest",
    "MaterializationResult",
    "ResourceMaterializationService",
]
