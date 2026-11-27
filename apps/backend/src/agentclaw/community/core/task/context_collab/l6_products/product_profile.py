"""Immutable persisted configuration. YAML is the sole stage graph input."""

from dataclasses import dataclass
from typing import TypeAlias
from enum import Enum
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    positive_id,
    text,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)

YAML: TypeAlias = str


class ProductProfileStatus(str, Enum):
    DRAFT = "DRAFT"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True)
class ProductProfile:
    id: int
    name: str
    composition: MethodCompositionTemplate
    stages: YAML

    def __post_init__(self) -> None:
        positive_id(self.id)
        text(self.name, maximum=256)
        text(self.stages)
        if type(self.composition) is not MethodCompositionTemplate:
            raise ContextCollabValidationError(
                "composition must be a MethodCompositionTemplate"
            )
