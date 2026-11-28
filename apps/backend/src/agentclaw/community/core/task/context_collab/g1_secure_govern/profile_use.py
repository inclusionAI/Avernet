"""Profile use is independent of publication, audit identity and run ownership."""

from typing import Protocol, runtime_checkable

from agentclaw.community.core.errors import Forbidden
from .actor_context import ActorContext
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)


@runtime_checkable
class ProductProfileUseAuthorizer(Protocol):
    def check_use(self, profile_id: int, context: ActorContext) -> None:
        """Check a deployment-owned grant in the trusted tenant/environment."""
        ...


class DenyProductProfileUse:
    def check_use(self, profile_id: int, context: ActorContext) -> None:
        raise Forbidden("Profile use authorization is not configured")


class AuthenticatedProductProfileUse:
    """Trust the existing authenticated actor scope; no extra allowlist gate."""

    def check_use(self, profile_id: int, context: ActorContext) -> None:
        PrincipalRef.from_dict(context.actor.to_dict())
