"""G1 structural authorization seam for Collet/Task/Event operations."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.collet_creation import (
    ColletCreationAuthorizer,
    StructuralColletCreationAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.legacy_collet import (
    LegacyCollet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    bound_executor_refs,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    METHOD_COMPOSITION_WIRES,
    MethodKind,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
)

VISIBILITY_PUBLIC = "public"
VISIBILITY_WORKSPACE = "workspace"
VISIBILITY_PRIVATE = "private"

TOP_LEVEL_METHOD_KINDS = frozenset(
    {
        MethodKind.SINGLE.value,
        MethodKind.SWARM.value,
        MethodKind.OPEN.value,
    }
) | METHOD_COMPOSITION_WIRES


@runtime_checkable
class ContextCollabAuthorizerProtocol(ColletCreationAuthorizer, Protocol):
    def check_create(
        self, *, method_kind: str, executor_ref, actor: ActorContext
    ) -> None: ...

    def check_append(
        self,
        collet: LegacyCollet,
        task: Task | None,
        creation_facts: dict,
        product_instance: ProductInstance | None,
        command,
        actor: ActorContext,
    ) -> None: ...

    def filter_visible_events(
        self,
        collet: LegacyCollet,
        task: Task | None,
        creation_facts: dict,
        product_instance: ProductInstance | None,
        events: Sequence[EventRecord],
        actor: ActorContext,
    ) -> list[EventRecord]: ...


def _is_principal(task: Task | None, actor: ActorContext) -> bool:
    return task is not None and actor.actor == task.principal_ref


def _is_executor(task: Task | None, actor: ActorContext) -> bool:
    return task is not None and any(
        actor.actor.principal_type == ref.agent_type
        and actor.actor.principal_id == ref.agent_id
        for ref in task.executor_refs
    )


def _is_public_open_message(creation_facts: dict, command) -> bool:
    return (
        creation_facts.get("method_kind") == "Open + Group"
        and command.event_kind == "user_interaction.posted"
        and command.primitive_type == "Message"
        and command.visibility == VISIBILITY_PUBLIC
    )


class StructuralContextCollabAuthorizer(
    StructuralColletCreationAuthorizer, ContextCollabAuthorizerProtocol
):
    """P1 structural rules; external membership/grants remain a replaceable seam."""

    def check_create(
        self, *, method_kind: str, executor_ref, actor: ActorContext
    ) -> None:
        if method_kind not in TOP_LEVEL_METHOD_KINDS:
            raise ContextCollabValidationError(
                f"top-level method_kind must be one of {sorted(TOP_LEVEL_METHOD_KINDS)}, "
                f"got: {method_kind!r}; "
                "bare Group may only enter via a nested method bind"
            )
        if method_kind == MethodKind.SINGLE.value and executor_ref is None:
            raise ContextCollabValidationError(
                "method_kind 'Single' requires a non-null executor_ref"
            )

    def check_append(
        self,
        collet: LegacyCollet,
        task: Task | None,
        creation_facts: dict,
        product_instance: ProductInstance | None,
        command,
        actor: ActorContext,
    ) -> None:
        if product_instance is not None and (
            actor.actor.principal_type in {"bot", "service"}
            or _is_executor(task, actor)
        ):
            product_instance.participant_scope.require(
                AgentRef(actor.actor.principal_type, actor.actor.principal_id)
            )
        if (
            _is_principal(task, actor)
            or _is_executor(task, actor)
            or _is_public_open_message(creation_facts, command)
        ):
            return
        raise ContextCollabValidationError(
            "only the principal, executor, or an Open+Group public-message contributor "
            f"may append events to collab_context {collet.collet_id!r}"
        )

    def filter_visible_events(
        self,
        collet: LegacyCollet,
        task: Task | None,
        creation_facts: dict,
        product_instance: ProductInstance | None,
        events: Sequence[EventRecord],
        actor: ActorContext,
    ) -> list[EventRecord]:
        is_principal = (
            _is_principal(task, actor)
            if task is not None
            else actor.actor == collet.initiator_ref
        )
        is_executor = (
            _is_executor(task, actor)
            if task is not None
            else AgentRef(actor.actor.principal_type, actor.actor.principal_id)
            in bound_executor_refs(events)
        )
        visible: list[EventRecord] = []
        for record in events:
            if record.visibility == VISIBILITY_PUBLIC:
                visible.append(record)
            elif record.visibility == VISIBILITY_WORKSPACE and (
                is_principal or is_executor
            ):
                visible.append(record)
            elif (
                record.visibility == VISIBILITY_PRIVATE or record.visibility is None
            ) and is_principal:
                visible.append(record)
        return visible
