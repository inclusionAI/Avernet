"""Typed method-specific product policies, declared by L6 rather than L5."""

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenPublicationProfile,
)


@dataclass(frozen=True)
class OpenMethodPolicy:
    """Product publication/admission policy for a required Open Method."""

    publication: OpenPublicationProfile
    method: Literal["Open"] = field(default="Open", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.publication, OpenPublicationProfile):
            raise ContextCollabValidationError(
                "Open Method policy requires an OpenPublicationProfile"
            )


# Only Open currently declares a method-specific product policy. Add another
# concrete typed variant here when that Method has a supported product policy.
MethodPolicy: TypeAlias = OpenMethodPolicy
