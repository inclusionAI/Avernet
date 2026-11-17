"""Uncommitted root Work construction; only Work.start may persist this draft."""

from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    initial_executor_refs,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.service_ports import (
    CollaborationReadState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    Task,
    TaskStatus,
)


def prepare_root_task(state: CollaborationReadState, command: WorkStartCommand) -> Task:
    """Real objective + installed L4 identities, not a placeholder or SQL write.

    The draft exists only in memory until G1/G4 accept start. Its creation and
    start evidence are persisted together behind the same Collet CAS.
    """
    if command.objective is None:
        raise ContextCollabValidationError(
            "Work.start requires objective to create Task"
        )
    event_id = "evt_" + uuid4().hex
    return Task(
        task_id="task_" + uuid4().hex,
        collet_ref=state.collet.collet_id,
        objective=command.objective,
        principal_ref=state.collet.initiator_ref,
        executor_refs=initial_executor_refs(state.events),
        status=TaskStatus.CREATED.value,
        version=1,
        parent_task_ref=None,
        completion_conditions=command.completion_conditions,
        created_event_ref=event_id,
        last_event_ref=event_id,
    )
