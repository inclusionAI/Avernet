"""Consumer contract for the Context collaboration runtime Plugin API."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

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
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
    EventAppendedRecord,
)
from agentclaw.community.core.task.context_collab.service_api import ActorContext
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from agentclaw.community.plugins.local.context_collab_runtime import (
    LocalContextCollabRuntimePlugin,
)


class RecordingContextRepository:
    """Minimal aggregate store that exposes the service's append seam."""

    def __init__(self) -> None:
        principal = PrincipalRef("human", "user_1")
        executor = AgentRef("bot", "bot_1")
        initial = EventRecord(
            collab_context_id="context_1",
            event_id="event_1",
            parent_event=None,
            event_kind="task.created",
            primitive_type="Task",
            actor_ref=principal,
            visibility="public",
            payload={"objective": "Run the Todo"},
            context_version_after=1,
            occurred_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
        )
        self.collet = Collet(
            collab_context_id="context_1",
            actor_ref=principal,
            context_spec=ContextSpec(
                space=SpaceRef("7", "test"),
                principal_ref=principal,
                method_kind="Single",
                executor_ref=executor,
                objective="Run the Todo",
                resource_refs=(ResourceRef("todo", "todo_1", None),),
                product_kind="team_todo",
                workflow_kind="team_todo_execution",
            ),
            current_snapshot=CurrentSnapshot(
                objective="Run the Todo",
                status="created",
                principal_ref=principal,
                executor_ref=executor,
                method_kind="Single",
                progress=ProgressSnapshot(current_event_id="event_1"),
            ),
            current_event_id="event_1",
            context_version=1,
        )
        self.events = [initial]

    def create_context(self, request):
        raise AssertionError("not used by the runtime contract")

    def get_current_status(self, collab_context_id: str):
        if collab_context_id != self.collet.collab_context_id:
            return None
        return ContextStatusRecord(collet=self.collet, events=list(self.events))

    def append_event(self, collab_context_id: str, request):
        assert collab_context_id == self.collet.collab_context_id
        assert request.expected_version == self.collet.context_version
        event = EventRecord(
            collab_context_id=collab_context_id,
            event_id="event_2",
            parent_event=request.parent_event,
            event_kind=request.event_kind,
            primitive_type=request.primitive_type,
            actor_ref=request.actor_ref,
            visibility=request.visibility,
            payload=request.payload,
            context_version_after=2,
            occurred_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
        )
        self.collet = replace(
            self.collet,
            current_snapshot=request.snapshot_updater(self.collet.current_snapshot),
            current_event_id=event.event_id,
            context_version=2,
        )
        self.events.append(event)
        return EventAppendedRecord(event=event, collet=self.collet)


@pytest.mark.asyncio
async def test_context_run_reaches_runtime_plugin_and_persists_started_event():
    repository = RecordingContextRepository()
    runtime = LocalContextCollabRuntimePlugin()
    service = ContextCollabService(repository=repository, runtime=runtime)

    result = await service.run_context(
        "context_1",
        ActorContext(actor=PrincipalRef("human", "user_1"), source="api"),
    )

    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.collab_context_id == "context_1"
    assert request.executor_id == "bot_1"
    assert request.principal_id == "user_1"
    assert request.objective == "Run the Todo"
    assert request.todo_id == "todo_1"
    assert result.run_id == "run_1"
    assert result.session_id == "session_1"
    assert result.context_version == 2
    assert result.current_status == "in_progress"
    assert repository.events[-1].event_kind == "task.started"
    assert repository.events[-1].actor_ref == PrincipalRef("bot", "bot_1")
    assert repository.events[-1].payload == {
        "run_id": "run_1",
        "session_id": "session_1",
    }
