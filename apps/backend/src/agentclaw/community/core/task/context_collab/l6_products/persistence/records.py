"""Product-aware SQL result contracts and decoder, owned by L6."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Self

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.views import (
    CurrentSnapshot,
    project_current_snapshot,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    COLLET_BINDING_EVENT,
    initial_executor_refs,
    initial_participation,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    InitialEventDraft as InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    PrimitiveReadState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    decode_creation_facts,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
    decode_product_instance,
)


@dataclass
class CreateColletRecordRequest:
    """Application persistence input; not the public L4 creation command."""

    space: SpaceRef
    principal_ref: PrincipalRef
    method_kind: str | None
    executor_ref: AgentRef | None
    objective: str
    resource_refs: tuple[ResourceRef, ...]
    product_kind: str | None
    workflow_kind: str | None
    product_profile_ref: VersionedProfileRef | None
    initial_event: InitialEventDraft
    completion_conditions: tuple[str, ...] = ()
    creation_facts: dict | None = None
    executor_refs: tuple[AgentRef, ...] = ()


class _RecordView:
    """Compatibility views generated from Collet/Task and plain facts."""

    collet: Collet
    creation_facts: dict
    task: Task | None

    @property
    def owner_principal_id(self) -> str:
        return self.collet.initiator_ref.principal_id

    @property
    def designated_executor_id(self) -> str | None:
        executor = (
            self.task.executor_ref
            if self.task is not None
            else AgentRef.from_dict(self.creation_facts["executor_ref"])
            if self.creation_facts.get("executor_ref") is not None
            else None
        )
        return executor.agent_id if executor is not None else None

    @property
    def current_snapshot(self) -> CurrentSnapshot | None:
        if self.task is None:
            return None
        return project_current_snapshot(
            self.task, creation_method_kind(self.creation_facts)
        )


@dataclass
class ContextCreatedRecord(_RecordView):
    """New Collet, identified Task and creation evidence in one transaction."""

    collet: Collet
    creation_facts: dict
    task: Task
    initial_event: EventRecord
    product_instance: ProductInstance | None = None


@dataclass
class ContextStatusRecord(_RecordView):
    """Current Collet, independent Task, event history and plain creation facts."""

    collet: Collet
    creation_facts: dict
    task: Task | None
    events: list[EventRecord]
    tasks: tuple[Task, ...] = ()
    product_instance: ProductInstance | None = None

    def with_task(self, task: Task) -> Self:
        """Uncommitted Work intent for policy validation, preserving all other facts."""
        return replace(self, task=task, tasks=(task,))

    @property
    def participants(self):
        return initial_participation(self.events).participants

    @property
    def role_bindings(self):
        return initial_participation(self.events).role_bindings


@dataclass
class EventAppendedRecord(_RecordView):
    """Saved Event, Collet revision and root Task; views are derived only."""

    event: EventRecord
    collet: Collet
    creation_facts: dict
    task: Task | None


class ProductRecordDecoder:
    """Decode product facts from the exact L4 SQL snapshot, with no extra lookup."""

    def __init__(self, catalog: ProductCatalog) -> None:
        self._catalog = catalog

    def validate(self, creation_facts_json: str) -> None:
        decode_creation_facts(json.loads(creation_facts_json))

    def decode(
        self, state: PrimitiveReadState, creation_facts_json: str
    ) -> ContextStatusRecord:
        facts = decode_creation_facts(json.loads(creation_facts_json))
        instance = decode_product_instance(
            facts,
            collet_id=state.collet.collet_id,
            root_task_id=state.task.task_id if state.task is not None else None,
            event_id=state.events[0].event_id if state.events else "",
            principal_ref=state.collet.initiator_ref,
            executor_ref=AgentRef.from_dict(facts["executor_ref"])
            if facts.get("executor_ref") is not None
            else None,
            catalog=self._catalog,
        )
        if instance is not None and (
            not state.events or instance.creation_event_ref != state.events[0].event_id
        ):
            raise ContextCollabValidationError(
                "ProductInstance creation evidence does not match initial event"
            )
        if instance is not None and facts["objective"] is None:
            evidence = state.events[0]
            expected_executors = (
                (AgentRef.from_dict(facts["executor_ref"]),)
                if facts["executor_ref"] is not None
                else ()
            )
            if (
                evidence.event_kind != COLLET_BINDING_EVENT
                or evidence.primitive_type != "Collet"
                or evidence.task_ref is not None
                or evidence.task_version_after is not None
                or initial_executor_refs(state.events) != expected_executors
            ):
                raise ContextCollabValidationError(
                    "ProductInstance does not match initial Collet binding evidence"
                )
        participation = initial_participation(state.events)
        if (
            instance is not None
            and "participant_scope" in facts["product_instance"]
            and not participation.participants
        ):
            raise ContextCollabValidationError(
                "ProductInstance requires actual L4 participation evidence"
            )
        if instance is not None and participation.participants:
            actual = tuple(
                role_binding_ref(state.collet.collet_id, b)
                for b in participation.role_bindings
            )
            if actual != instance.role_binding_refs:
                raise ContextCollabValidationError(
                    "ProductInstance references missing L4 RoleBindings"
                )
            for participant in participation.participants:
                if participant.agent_ref is not None:
                    instance.participant_scope.require(participant.agent_ref)
        return ContextStatusRecord(
            collet=state.collet,
            creation_facts=facts,
            task=state.task,
            tasks=state.tasks,
            events=state.events,
            product_instance=instance,
        )
