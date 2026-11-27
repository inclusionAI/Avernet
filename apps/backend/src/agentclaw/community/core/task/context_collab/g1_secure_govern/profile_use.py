"""Profile use is independent of publication, audit identity and run ownership."""

from typing import Protocol, runtime_checkable

from agentclaw.community.core.errors import Forbidden
from .actor_context import ActorContext


@runtime_checkable
class ProductProfileUseAuthorizer(Protocol):
    def check_use(self, profile_id: int, context: ActorContext) -> None:
        """Check a deployment-owned grant in the trusted tenant/environment."""
        ...


class DenyProductProfileUse:
    def check_use(self, profile_id: int, context: ActorContext) -> None:
        raise Forbidden("Profile use authorization is not configured")
