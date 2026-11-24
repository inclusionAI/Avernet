"""Seed L5 HTTP cases through real L4 and Method Service APIs, never store writes."""

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ColletCreateCommand,
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    BindMethodCommand,
    MethodLifecycleServiceProtocol,
    MethodStateChangeCommand,
)
from tests.community.factories.access import make_staff_user

BASE = "/api/v1/collaboration/task/context_collab/{collab_context_id}/method-bindings"
BINDING = "mb_http_case"
OWNER = ActorContext(PrincipalRef("human", "u_method_case"), "api")
HEADERS = {"x-user-id": OWNER.actor.principal_id}


def seed_method(world, kind: str, *, active: bool = True) -> str:
    make_staff_user(world, user_id=OWNER.actor.principal_id)
    collet = world.get(ContextServiceProtocol).create_collet(
        ColletCreateCommand(OWNER.actor, title="L5 HTTP regression"), OWNER
    )
    lifecycle = world.get(MethodLifecycleServiceProtocol)
    lifecycle.bind(
        collet.collet_id,
        BindMethodCommand(
            method_kind=kind,
            method_binding_id=BINDING,
            parent_scope_ref="scope://http",
        ),
        OWNER,
    )
    if active:
        lifecycle.activate(collet.collet_id, BINDING, MethodStateChangeCommand(), OWNER)
    return collet.collet_id
