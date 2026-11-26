"""Independent product iteration, not a renamed Method Plan view."""

from dataclasses import dataclass
from agentclaw.community.core.task.context_collab.l5_methods.stage_rollout import (
    StageRollout,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)


@dataclass(frozen=True)
class ColletRollout:
    collet_id: int
    rollout_id: int
    stages: list[StageRollout]
    content: str
    status: StatusEnum

    def __post_init__(self) -> None:
        from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
            positive_id,
            text,
        )
        from agentclaw.community.core.task.context_collab.errors import (
            ContextCollabValidationError,
        )

        positive_id(self.collet_id)
        positive_id(self.rollout_id)
        text(self.content)
        if (
            type(self.status) is not StatusEnum
            or type(self.stages) is not list
            or any(type(s) is not StageRollout for s in self.stages)
        ):
            raise ContextCollabValidationError("invalid rollout status or stages")
