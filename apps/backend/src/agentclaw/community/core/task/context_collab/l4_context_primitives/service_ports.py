"""L4-owned storage and G1/G4 extension contracts, not application callbacks.

L4 owns the complete operation pipeline. A policy may reject an intent, filter
visible events, or select a G4 machine, but cannot prepare/commit Work or return
a service result. Policy errors propagate before writes. Ports use the exact
same read snapshot so Task authority, evidence and policy cannot drift apart.

StateT permits an adapter to consume additional stored facts without making L4
import or interpret them. A store and its policy must agree on that subtype;
L4 only reads the lower-layer CollaborationReadState properties below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, TypeVar

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.views import (
    CurrentSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletWriter,
    EventAppendCommand,
    EventWriter,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task


class CollaborationReadState(Protocol):
    """Lower-only read facts and a derived G3 response, never Work authority."""

    @property
    def collet(self) -> Collet: ...
    @property
    def task(self) -> Task | None: ...
    @property
    def tasks(self) -> tuple[Task, ...]: ...
    @property
    def events(self) -> Sequence[EventRecord]: ...
    @property
    def current_snapshot(self) -> CurrentSnapshot | None:
        """Compatibility display view; must be present when task is present."""
        ...


ReadStateT_co = TypeVar("ReadStateT_co", bound=CollaborationReadState, covariant=True)
PolicyStateT_contra = TypeVar(
    "PolicyStateT_contra", bound=CollaborationReadState, contravariant=True
)


class CollaborationStore(ColletWriter, EventWriter, Protocol[ReadStateT_co]):
    def get_current_status(self, collab_context_id: str) -> ReadStateT_co | None: ...


@dataclass(frozen=True)
class WorkLifecycle:
    """Resolved G4 machine selector; None means no selected lifecycle machine.

    This is not a Product/Workflow Profile reference. Only the G4 registry's
    machine identifier and optional pinned version cross into L4 Work.
    """

    workflow_kind: str | None
    workflow_version: int | None = None


class CollaborationPolicy(Protocol[PolicyStateT_contra]):
    """Read-only G1/G4 hooks. Schema -> G1 -> revision -> policy -> L4 Work/CAS."""

    def check_append(
        self,
        state: PolicyStateT_contra,
        command: EventAppendCommand,
        context: ActorContext,
    ) -> None:
        """G1 authorization, including any deployment-selected admission guard."""
        ...

    def validate_append(
        self,
        state: PolicyStateT_contra,
        command: EventAppendCommand,
        context: ActorContext,
    ) -> WorkLifecycle:
        """Validate additional contracts/evidence and return the pinned G4 machine."""
        ...

    def filter_visible_events(
        self, state: PolicyStateT_contra, context: ActorContext
    ) -> list[EventRecord]:
        """G1-filter accepted events in their original order; never write state."""
        ...
