"""Worker progress facts. Authentication/idempotency never come from the body."""

from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    EventTypeEnum,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    positive_id,
    text,
    shape,
    MAX_CONTENT_BYTES,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)


@dataclass(frozen=True)
class ProgressEvent:
    type: EventTypeEnum
    stage_rollout_id: int
    artifacts: list[Artifact]
    acceptances: list[AcceptanceResult]
    error_msg: str

    def __post_init__(self) -> None:
        positive_id(self.stage_rollout_id)
        text(self.error_msg, empty=self.type is not EventTypeEnum.RUN_FAILED)
        if type(self.type) is not EventTypeEnum:
            raise Invalid("invalid ProgressEvent type")
        for values, cls in (
            (self.artifacts, Artifact),
            (self.acceptances, AcceptanceResult),
        ):
            if (
                type(values) is not list
                or len(values) > 100
                or any(type(v) is not cls for v in values)
            ):
                raise Invalid("invalid progress evidence")
        if len(json.dumps(asdict(self)).encode()) > MAX_CONTENT_BYTES:
            raise Invalid("progress evidence exceeds byte limit")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ProgressEvent:
        shape(
            data, {"type", "stage_rollout_id", "artifacts", "acceptances", "error_msg"}
        )
        if any(
            type(data[name]) is not list or len(data[name]) > 100
            for name in ("artifacts", "acceptances")
        ):
            raise Invalid("progress evidence must be bounded lists")
        try:
            kind = EventTypeEnum(data["type"])
            acceptances = []
            for item in data["acceptances"]:
                shape(item, {"condition", "passed", "reason"})
                acceptances.append(AcceptanceResult(**item))
            return cls(
                kind,
                data["stage_rollout_id"],
                [Artifact.from_dict(a) for a in data["artifacts"]],
                acceptances,
                data["error_msg"],
            )
        except (ValueError, TypeError):
            # Enum/constructor errors can embed caller-supplied inline values.
            raise Invalid("invalid progress event") from None
