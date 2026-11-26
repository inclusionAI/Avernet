"""L4 SQL records and a read-only storage decoder extension.

The service sees only CollaborationReadState. Serialized creation metadata is
passed to a decoder inside persistence, never interpreted by the L4 service.
A deployment can validate/project a richer state from the same SQL read without
a second lookup. Creation never invokes this extension.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Protocol, Self, TypeVar, runtime_checkable

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
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    current_participation,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.service_ports import (
    CollaborationReadState,
    CollaborationStore,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    normalize_executor_refs,
)


@dataclass
class InitialEventDraft:
    event_kind: str
    primitive_type: str
    actor_ref: PrincipalRef
    payload: dict


@dataclass
class CreatedColletRecord:
    collet: Collet


@dataclass
class CreatedWorkRecord:
    collet: Collet
    task: Task
    initial_event: EventRecord


@dataclass
class StoredEventAppend:
    event: EventRecord
    collet: Collet
    task: Task | None
    creation_facts_json: str


@dataclass
class PrimitiveReadState:
    collet: Collet
    task: Task | None
    tasks: tuple[Task, ...]
    events: list[EventRecord]

    def with_task(self, task: Task) -> Self:
        """Uncommitted Work intent for policy validation, preserving all other facts."""
        return replace(self, task=task, tasks=(task,))

    @property
    def participants(self):
        return current_participation(self.events).participants

    @property
    def role_bindings(self):
        return current_participation(self.events).role_bindings

    @property
    def owner_principal_id(self) -> str:
        return self.collet.initiator_ref.principal_id

    @property
    def designated_executor_id(self) -> str | None:
        """Compatibility identity view; many executors have no sole executor."""
        executor = self.task.executor_ref if self.task is not None else None
        return executor.agent_id if executor is not None else None

    @property
    def current_snapshot(self) -> CurrentSnapshot | None:
        return project_current_snapshot(self.task, None)


ReadStateT_co = TypeVar("ReadStateT_co", bound=CollaborationReadState, covariant=True)


class CollaborationRecordDecoder(Protocol[ReadStateT_co]):
    def validate(self, creation_facts_json: str) -> None: ...

    def decode(
        self, state: PrimitiveReadState, creation_facts_json: str
    ) -> ReadStateT_co: ...


def decode_primitive_creation_facts(creation_facts_json: str) -> dict:
    facts = json.loads(creation_facts_json)
    if facts == {}:
        return {}  # A newly created Collet has no Work facts.
    if (
        not isinstance(facts, dict)
        or set(facts) != {"objective", "executor_refs"}
        or not isinstance(facts["objective"], str)
        or not facts["objective"].strip()
        or not isinstance(facts["executor_refs"], list)
        or any(not isinstance(ref, dict) for ref in facts["executor_refs"])
    ):
        raise ContextCollabValidationError(
            "invalid method-independent L4 creation facts"
        )
    refs = normalize_executor_refs(
        [AgentRef.from_dict(ref) for ref in facts["executor_refs"]]
    )
    return {
        "objective": facts["objective"],
        "executor_refs": [ref.to_dict() for ref in refs],
    }


class PrimitiveRecordDecoder:
    def validate(self, creation_facts_json: str) -> None:
        decode_primitive_creation_facts(creation_facts_json)

    def decode(
        self, state: PrimitiveReadState, creation_facts_json: str
    ) -> PrimitiveReadState:
        self.validate(creation_facts_json)
        return state


@runtime_checkable
class ContextCollabRepositoryProtocol(
    CollaborationStore[ReadStateT_co], Protocol[ReadStateT_co]
):
    """L4 creation, atomic append and authorized-read storage contract."""
