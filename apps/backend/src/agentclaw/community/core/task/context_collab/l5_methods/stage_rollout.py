"""Persisted stage execution value; no synthetic session identifiers."""

from dataclasses import dataclass
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)


@dataclass(frozen=True)
class StageRollout:
    name: str
    stage_rollout_id: int
    status: StatusEnum
    assignee_type: str
    assignee_uuid: str
    input: str
    session_id: str
    artifacts: list[Artifact]
    acceptances: list[AcceptanceResult]
    error_msg: str

    def __post_init__(self) -> None:
        from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
            positive_id,
            text,
        )
        from agentclaw.community.core.task.context_collab.errors import (
            ContextCollabValidationError,
        )

        positive_id(self.stage_rollout_id)
        text(self.name, maximum=256)
        for value in (
            self.assignee_type,
            self.assignee_uuid,
            self.input,
            self.session_id,
            self.error_msg,
        ):
            text(value, empty=True)
        if (
            type(self.status) is not StatusEnum
            or type(self.artifacts) is not list
            or type(self.acceptances) is not list
        ):
            raise ContextCollabValidationError(
                "invalid stage status or evidence collections"
            )
        if any(type(a) is not Artifact for a in self.artifacts) or any(
            type(a) is not AcceptanceResult for a in self.acceptances
        ):
            raise ContextCollabValidationError("invalid stage evidence values")
