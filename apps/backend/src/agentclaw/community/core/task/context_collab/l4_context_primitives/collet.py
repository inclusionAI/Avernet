"""Product collaboration request. Technical authority lives in storage records."""

from dataclasses import dataclass
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    positive_id,
    text,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


@dataclass(frozen=True)
class Collet:
    id: int
    profile_id: int
    content: str
    status: StatusEnum

    def __post_init__(self) -> None:
        positive_id(self.id)
        positive_id(self.profile_id)
        text(self.content)
        if type(self.status) is not StatusEnum:
            raise ContextCollabValidationError("invalid Collet status")
