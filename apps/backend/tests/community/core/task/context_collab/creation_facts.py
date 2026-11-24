"""Test builders for plain ACCS creation facts.

These helpers intentionally return dictionaries.  L4 Collet/Task and L6
selection are constructed independently by each test; there is no aggregate
creation object in the protocol model.
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)


def creation_facts(
    *,
    objective: str,
    method_kind: str,
    executor_ref: AgentRef | None,
    product_kind: str | None,
    workflow_kind: str | None,
    resource_refs: tuple[ResourceRef, ...] | list[ResourceRef] = (),
    product_profile_ref: VersionedProfileRef | dict | None = None,
    **_legacy_scope_fields: object,
) -> dict:
    if isinstance(product_profile_ref, VersionedProfileRef):
        product_profile_ref = {
            "profile_id": product_profile_ref.profile_id,
            "version": product_profile_ref.version,
        }
    return {
        "objective": objective,
        "resource_refs": [ref.to_dict() for ref in resource_refs],
        "method_kind": method_kind,
        "executor_ref": executor_ref.to_dict() if executor_ref else None,
        "product_kind": product_kind,
        "workflow_kind": workflow_kind,
        "product_profile_ref": product_profile_ref,
    }


def create_collet_request(
    facts: dict,
    *,
    space,
    principal_ref,
    initial_event,
    completion_conditions: tuple[str, ...] = (),
):
    """Build the flat repository request from plain creation facts."""
    from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
        AgentRef,
    )
    from agentclaw.community.core.task.context_collab.persistence.repository import (
        CreateColletRecordRequest,
    )

    raw_executor = facts.get("executor_ref")
    return CreateColletRecordRequest(
        space=space,
        principal_ref=principal_ref,
        method_kind=facts["method_kind"],
        executor_ref=AgentRef.from_dict(raw_executor) if raw_executor else None,
        objective=facts["objective"],
        resource_refs=tuple(
            ResourceRef.from_dict(resource) for resource in facts["resource_refs"]
        ),
        product_kind=facts["product_kind"],
        workflow_kind=facts["workflow_kind"],
        product_profile_ref=(
            VersionedProfileRef(**facts["product_profile_ref"])
            if facts.get("product_profile_ref") is not None
            else None
        ),
        creation_facts=facts,
        initial_event=initial_event,
        completion_conditions=completion_conditions,
    )


def creation_event_id(
    service: ContextServiceProtocol, created: Collet, context: ActorContext
) -> str:
    """Resolve the combined L6 flow's immutable Task creation evidence.

    A Collet does not own an event-head pointer. Filter by the returned creation
    revision so later appends cannot change the parent used by a test.
    """
    [event] = [
        event
        for event in service.get_events(created.collet_id, context=context)
        if event.context_version_after == created.version
        and event.event_kind == "task.created"
    ]
    return event.event_id
