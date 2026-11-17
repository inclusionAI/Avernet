"""Shared G4 enforcement for generic and product entrypoints."""

from __future__ import annotations

from typing import Sequence

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    VERSIONED_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    actor_has_role,
    product_definition,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    creation_product_kind,
    creation_profile_ref,
    creation_workflow_kind,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    TASK_PROGRESS_DETAIL_FIELDS,
    resolve_event_contract,
)


def validate_product_event(
    *,
    creation_facts: dict,
    product_instance: ProductInstance | None,
    catalog: ProductCatalog,
    task: Task | None,
    event_kind: str,
    primitive_type: str,
    visibility: str,
    payload: dict,
    actor,
    parent_event: str | None = None,
    events: Sequence[EventRecord] = (),
) -> None:
    profile = product_definition(
        product_kind=creation_product_kind(creation_facts),
        workflow_kind=creation_workflow_kind(creation_facts),
        method_kind=creation_method_kind(creation_facts),
        profile_ref=creation_profile_ref(creation_facts),
        catalog=catalog,
    )
    if profile is None:
        return
    contract = resolve_event_contract(profile)
    if contract.public_events_only and visibility != "public":
        raise ContextCollabValidationError("product events must be public")
    if (
        profile.workflow is not None
        and profile.workflow.message_parent_only
        and event_kind == "user_interaction.posted"
        and parent_event is not None
    ):
        target = next((item for item in events if item.event_id == parent_event), None)
        if target is not None and target.primitive_type != "Message":
            raise ContextCollabValidationError(
                "parent_event is not a Message event; replies may only reply to user postings"
            )
    if contract.executor_submission_only and "percent" in payload:
        percent = payload["percent"]
        if type(percent) is not int or not 0 <= percent <= 100:
            raise ContextCollabValidationError(
                "task percent must be an integer in [0, 100]"
            )
        if event_kind != "task.progress.updated":
            raise ContextCollabValidationError(
                "task percent is only reportable by report_progress"
            )
    if contract.executor_submission_only and event_kind.startswith("task."):
        for field_name in TASK_PROGRESS_DETAIL_FIELDS:
            if field_name not in payload:
                continue
            if event_kind != "task.progress.updated":
                raise ContextCollabValidationError(
                    f"task {field_name} is only reportable by report_progress"
                )
            if not isinstance(payload[field_name], str):
                raise ContextCollabValidationError(
                    f"task {field_name} must be a string"
                )
        if "task_id" in payload and (
            task is None or payload["task_id"] != task.task_id
        ):
            raise ContextCollabValidationError(
                "report task_id does not match the L4 Task"
            )
    machine_ref = contract.state_machine_ref
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
        (action for action in contract.actions if action.event_kind == event_kind), None
    )
    if declared is not None and declared.required_role is not None:
        has_role = actor_has_role(task, actor, declared.required_role, profile, events)
        if not has_role:
            raise ContextCollabValidationError(
                "actor is not the designated executor for this workflow action"
            )
    if not contract.strict_control_events or not event_kind.startswith("task."):
        return
    if machine is None or not any(
        rule.event_name == event_kind for rule in machine.transitions
    ):
        raise ContextCollabValidationError(
            f"product workflow does not permit control event {event_kind!r}"
        )
    if declared is None:
        raise ContextCollabValidationError(
            f"product workflow has no declared control event {event_kind!r}"
        )
    if primitive_type != declared.primitive_type:
        raise ContextCollabValidationError(
            f"product control event requires primitive_type {declared.primitive_type}"
        )
    if task is None:
        raise ContextCollabValidationError(
            "cannot advance task: no L4 Task to adjudicate"
        )
    if not any(
        rule.event_name == event_kind and task.status in rule.from_states
        for rule in machine.transitions
    ):
        raise ContextCollabValidationError(
            f"cannot advance task: {event_kind!r} is not allowed from status {task.status!r}"
        )
    if contract.executor_submission_only and primitive_type != "Task":
        raise ContextCollabValidationError(
            "task commission control events require primitive_type Task"
        )
