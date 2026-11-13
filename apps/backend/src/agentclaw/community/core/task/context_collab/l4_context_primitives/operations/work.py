"""L4 Work resolution and atomic append, shared by generic and product callers.

Application callers authorize and validate product/schema contracts first, and
resolve a machine ID/version outside L4. Preparation adjudicates G4 and fences
the exact Task. An application deadline can still reject before commit. No L6
profile, transport, admission or business action names are interpreted here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.workflow_resolution import (
    resolve_workflow_status,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendedEvent,
    AppendEventRecordRequest,
    EventWriter,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    TaskUpdate,
)


@dataclass(frozen=True)
class PreparedEventAppend:
    """Authorized application's resolved intent; commit preserves repository errors/CAS."""

    collab_context_id: str
    request: AppendEventRecordRequest

    def commit(self, writer: EventWriter) -> AppendedEvent:
        return writer.append_event(self.collab_context_id, self.request)


def prepare_event_append(
    collab_context_id: str,
    request: AppendEventRecordRequest,
    *,
    task: Task | None,
    actor: PrincipalRef,
    workflow_kind: str | None,
    workflow_version: int | None = None,
) -> PreparedEventAppend:
    """Adjudicate Work; ordinary events never acquire a TaskUpdate.

    Unknown/unmatched machine events retain existing G4 semantics. Public entry
    points are responsible for product-specific strict sequence checks and G1.
    """
    if request.task_update is not None:
        raise ContextCollabValidationError("TaskUpdate must be resolved by L4 Work")
    resolved = resolve_workflow_status(
        workflow_kind=workflow_kind,
        workflow_version=workflow_version,
        task=task,
        event_kind=request.event_kind,
        payload=request.payload,
        actor=actor,
    )
    is_work = request.event_kind.startswith("task.") and request.primitive_type in {
        "Task",
        "Activity",
    }
    if is_work and task is None:
        raise ContextCollabValidationError("cannot advance Work without an L4 Task")
    update = None
    if is_work and task is not None:
        update = TaskUpdate(
            task.task_id,
            task.version,
            resolved if resolved is not None else task.status,
        )
    return PreparedEventAppend(collab_context_id, replace(request, task_update=update))
