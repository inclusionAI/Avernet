"""Publication authority is deliberately separate from Collet creation rights."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)


@runtime_checkable
class ProductProfilePublicationAuthorizer(Protocol):
    def check_publish(
        self, profile_id: str, version: int, actor: ActorContext
    ) -> None: ...


@dataclass(frozen=True)
class ProfilePublisherAllowlist:
    """Deployment-owned trusted subjects. Empty is deny-all, not public write."""

    principals: tuple[PrincipalRef, ...] = ()

    def check_publish(self, profile_id: str, version: int, actor: ActorContext) -> None:
        if actor.actor not in self.principals:
            raise ContextCollabValidationError(
                "ProductProfile publication is not authorized"
            )
