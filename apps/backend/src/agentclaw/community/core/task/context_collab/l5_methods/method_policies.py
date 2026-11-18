"""L5-owned Method policy values for composition by callers, including L6."""

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    OpenPublicationProfile,
)


@dataclass(frozen=True)
class OpenMethodPolicy:
    """Publication/admission policy for an Open Method."""

    publication: OpenPublicationProfile
    method: Literal["Open"] = field(default="Open", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.publication, OpenPublicationProfile):
            raise ContextCollabValidationError(
                "Open Method policy requires an OpenPublicationProfile"
            )


# Only Open currently declares a Method policy. Add another concrete typed
# variant here when that Method has a supported policy.
MethodPolicy: TypeAlias = OpenMethodPolicy
