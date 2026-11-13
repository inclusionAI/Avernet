"""Shared G4 enforcement for generic and product entrypoints; no write-path bypass."""

from __future__ import annotations

from typing import Sequence

from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    VERSIONED_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ContextSpec,
    CurrentSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ROLE_SELECTED_AGENT,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    actor_has_role,
    validate_product_spec,
)


def validate_product_event(
    *,
    spec: ContextSpec,
    catalog: ProductCatalog,
    snapshot: CurrentSnapshot | None,
    event_kind: str,
    primitive_type: str,
    visibility: str,
    payload: dict,
    actor,
    parent_event: str | None = None,
    events: Sequence[EventRecord] = (),
) -> None:
    profile = validate_product_spec(spec, catalog)
    if profile is None:
        return  # Existing generic L4 behavior is unchanged.
    workflow = profile.workflow
    if workflow.public_events_only and visibility != "public":
        raise ContextCollabValidationError("product events must be public")
    if (
        workflow.message_parent_only
        and event_kind == "user_interaction.posted"
        and parent_event is not None
    ):
        target = next((item for item in events if item.event_id == parent_event), None)
        if target is not None and target.primitive_type != "Message":
            raise ContextCollabValidationError(
                "parent_event is not a Message event; replies may only reply to user postings"
            )
    # Evidence fields must not be smuggled through a nontransition Message.
    if workflow.executor_submission_only and "percent" in payload:
        percent = payload["percent"]
        if type(percent) is not int or not 0 <= percent <= 100:
            raise ContextCollabValidationError(
                "task percent must be an integer in [0, 100]"
            )
        if event_kind != "task.progress.updated":
            raise ContextCollabValidationError(
                "task percent is only reportable by report_progress"
            )
    machine_ref = workflow.state_machine_ref
    machine = (
        VERSIONED_MACHINE_REGISTRY.get((machine_ref.profile_id, machine_ref.version))
        if machine_ref
        else None
    )
    if machine_ref is not None and machine is None:
        raise ContextCollabValidationError(
            "required versioned workflow machine is unavailable"
        )
    declared = next(
        (action for action in workflow.actions if action.event_kind == event_kind),
        None,
    )
    if declared is not None and declared.required_role is not None:
        has_role = actor_has_role(spec, actor, declared.required_role)
        # Historical unversioned instances have no materialized role facts.
        if (
            spec.product_profile_version is None
            and declared.required_role == ROLE_SELECTED_AGENT
            and spec.executor_ref is not None
        ):
            has_role = (
                actor.principal_type == spec.executor_ref.agent_type
                and actor.principal_id == spec.executor_ref.agent_id
            )
        if not has_role:
            raise ContextCollabValidationError(
                "actor is not the designated executor for this workflow action"
            )
    if not workflow.strict_control_events or not event_kind.startswith("task."):
        return  # Preserve established nontransition semantics unless the profile opts into strict Work.
    if machine is None or not any(
        rule.event_name == event_kind for rule in machine.transitions
    ):
        raise ContextCollabValidationError(
            f"product workflow does not permit control event {event_kind!r}"
        )
    if workflow.actions:
        if declared is None:
            raise ContextCollabValidationError(
                f"product workflow has no declared control event {event_kind!r}"
            )
        if primitive_type != declared.primitive_type:
            raise ContextCollabValidationError(
                f"product control event requires primitive_type {declared.primitive_type}"
            )
    if snapshot is None:
        raise ContextCollabValidationError(
            "cannot advance task: no snapshot to adjudicate"
        )
    if not any(
        rule.event_name == event_kind and snapshot.status in rule.from_states
        for rule in machine.transitions
    ):
        raise ContextCollabValidationError(
            f"cannot advance task: {event_kind!r} is not allowed from status {snapshot.status!r}"
        )
    if workflow.executor_submission_only:
        if primitive_type != "Task":
            raise ContextCollabValidationError(
                "task commission control events require primitive_type Task"
            )
        # Unversioned legacy rows have no persisted RoleBinding facts. Preserve
        # their typed executor check without selecting a second/global catalog.
        is_executor = actor_has_role(spec, actor, ROLE_SELECTED_AGENT)
        if spec.product_profile_version is None and spec.executor_ref is not None:
            is_executor = (
                actor.principal_type == spec.executor_ref.agent_type
                and actor.principal_id == spec.executor_ref.agent_id
            )
        if event_kind == "task.submitted" and not is_executor:
            raise ContextCollabValidationError(
                "cannot submit task: actor is not the designated executor"
            )
