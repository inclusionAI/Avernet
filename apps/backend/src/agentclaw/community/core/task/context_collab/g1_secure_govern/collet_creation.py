"""Method-independent G1 creation authorization seam."""

from typing import Protocol

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
)


class ColletCreationAuthorizer(Protocol):
    def check_create_collet(
        self, *, command: ColletCreateCommand, actor: ActorContext
    ) -> None: ...


class StructuralColletCreationAuthorizer:
    """P1 authenticated structural check, replaceable by membership/grant policy.

    Responsible principal may differ from the initiating actor, as in the
    existing application creation flow. No Method-specific cardinality here.
    """

    def check_create_collet(
        self, *, command: ColletCreateCommand, actor: ActorContext
    ) -> None:
        if not isinstance(actor, ActorContext) or not isinstance(
            actor.actor, PrincipalRef
        ):
            raise ContextCollabValidationError(
                "Collet creation requires an authenticated actor"
            )
        PrincipalRef.from_dict(actor.actor.to_dict())
        if not isinstance(actor.source, str) or not actor.source.strip():
            raise ContextCollabValidationError(
                "Collet creation requires an actor source"
            )
