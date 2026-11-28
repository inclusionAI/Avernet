"""Acceptance declarations, independently checked by the trusted validator."""

from dataclasses import dataclass
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    text,
)


@dataclass(frozen=True)
class AcceptanceResult:
    condition: str
    passed: bool
    reason: str

    def __post_init__(self) -> None:
        text(self.condition)
        text(self.reason, empty=self.passed is True)
        if type(self.passed) is not bool:
            raise ContextCollabValidationError("passed must be a boolean")
