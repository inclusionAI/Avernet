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
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
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
                executor_owner_id="another_user",
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
        next_version = self.collet.context_version + 1
        event = EventRecord(
            collab_context_id=collab_context_id,
            event_id=f"event_{next_version}",
            parent_event=request.parent_event,
            event_kind=request.event_kind,
            primitive_type=request.primitive_type,
            actor_ref=request.actor_ref,
            visibility=request.visibility,
            payload=request.payload,
            context_version_after=next_version,
            occurred_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
        )
        self.collet = replace(
            self.collet,
            current_snapshot=request.snapshot_updater(self.collet.current_snapshot),
            current_event_id=event.event_id,
            context_version=next_version,
        )
        self.events.append(event)
        return EventAppendedRecord(event=event, collet=self.collet)


class ConcurrentCompletionRepository(RecordingContextRepository):
    """Simulate another worker winning the completion append race."""

    def append_event(self, collab_context_id: str, request):
        record = super().append_event(collab_context_id, request)
        if request.event_kind == "task.completed":
            raise ContextCollabVersionConflict("concurrent completion")
        return record


class StaleConflictRepository(RecordingContextRepository):
    """Raise a version conflict without advancing the aggregate."""

    def append_event(self, collab_context_id: str, request):
        if request.event_kind == "task.completed":
            raise ContextCollabVersionConflict("stale writer")
        return super().append_event(collab_context_id, request)


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
    assert request.executor_owner_id == "another_user"
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


@pytest.mark.asyncio
async def test_local_runtime_exposes_running_status_after_start():
    repository = RecordingContextRepository()
    runtime = LocalContextCollabRuntimePlugin()
    service = ContextCollabService(repository=repository, runtime=runtime)

    await service.run_context(
        "context_1",
        ActorContext(actor=PrincipalRef("human", "user_1"), source="api"),
    )

    run = await runtime.get_run("run_1")

    assert run.run_id == "run_1"
    assert run.status == "RUNNING"
    assert run.session_id == "session_1"
    assert runtime.get_run_requests == ["run_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_status", ["PENDING", "RUNNING"])
async def test_refresh_keeps_non_terminal_runtime_in_progress(runtime_status: str):
    repository = RecordingContextRepository()
    runtime = LocalContextCollabRuntimePlugin()
    service = ContextCollabService(repository=repository, runtime=runtime)
    actor = ActorContext(actor=PrincipalRef("human", "user_1"), source="api")
    await service.run_context("context_1", actor)
    runtime.set_run_status_for_test("run_1", status=runtime_status)

    projection = await service.refresh_run("context_1", actor)

    assert projection.current_snapshot["status"] == "in_progress"
    assert len(repository.events) == 2
    assert runtime.get_run_requests == ["run_1"]


@pytest.mark.asyncio
async def test_refresh_completed_runtime_appends_once_and_is_idempotent():
    repository = RecordingContextRepository()
    runtime = LocalContextCollabRuntimePlugin()
    service = ContextCollabService(repository=repository, runtime=runtime)
    actor = ActorContext(actor=PrincipalRef("human", "user_1"), source="api")
    await service.run_context("context_1", actor)
    runtime.set_run_status_for_test(
        "run_1",
        status="COMPLETED",
        result={"content": "done"},
        completed_at="2026-11-12T04:00:00Z",
        session_id="session_1",
    )

    completed = await service.refresh_run("context_1", actor)
    repeated = await service.refresh_run("context_1", actor)

    assert completed.current_snapshot["status"] == "done"
    assert repeated.current_snapshot["status"] == "done"
    assert len(repository.events) == 3
    event = repository.events[-1]
    assert event.event_kind == "task.completed"
    assert event.actor_ref == PrincipalRef("bot", "bot_1")
    assert event.payload == {
        "run_id": "run_1",
        "result": {"content": "done"},
        "session_id": "session_1",
        "completed_at": "2026-11-12T04:00:00Z",
    }
    assert runtime.get_run_requests == ["run_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_status", ["FAILED", "TIME_OUT", "ABORTED"])
async def test_refresh_failed_terminal_runtime_does_not_complete_context(
    runtime_status: str,
):
    repository = RecordingContextRepository()
    runtime = LocalContextCollabRuntimePlugin()
    service = ContextCollabService(repository=repository, runtime=runtime)
    actor = ActorContext(actor=PrincipalRef("human", "user_1"), source="api")
    await service.run_context("context_1", actor)
    runtime.set_run_status_for_test(
        "run_1",
        status=runtime_status,
        error={"message": "runtime failed"},
    )

    projection = await service.refresh_run("context_1", actor)

    assert projection.current_snapshot["status"] == "in_progress"
    assert len(repository.events) == 2


@pytest.mark.asyncio
async def test_refresh_rejects_in_progress_context_without_started_run_id():
    repository = RecordingContextRepository()
    repository.collet = replace(
        repository.collet,
        current_snapshot=replace(
            repository.collet.current_snapshot,
            status="in_progress",
        ),
    )
    runtime = LocalContextCollabRuntimePlugin()
    service = ContextCollabService(repository=repository, runtime=runtime)

    with pytest.raises(ContextCollabValidationError, match="task.started.*run_id"):
        await service.refresh_run(
            "context_1",
            ActorContext(actor=PrincipalRef("human", "user_1"), source="api"),
        )

    assert runtime.get_run_requests == []


@pytest.mark.asyncio
async def test_refresh_treats_concurrent_done_transition_as_idempotent_success():
    repository = ConcurrentCompletionRepository()
    runtime = LocalContextCollabRuntimePlugin()
    service = ContextCollabService(repository=repository, runtime=runtime)
    actor = ActorContext(actor=PrincipalRef("human", "user_1"), source="api")
    await service.run_context("context_1", actor)
    runtime.set_run_status_for_test("run_1", status="COMPLETED")

    projection = await service.refresh_run("context_1", actor)

    assert projection.current_snapshot["status"] == "done"
    assert len(repository.events) == 3


@pytest.mark.asyncio
async def test_refresh_propagates_version_conflict_when_context_is_not_done():
    repository = StaleConflictRepository()
    runtime = LocalContextCollabRuntimePlugin()
    service = ContextCollabService(repository=repository, runtime=runtime)
    actor = ActorContext(actor=PrincipalRef("human", "user_1"), source="api")
    await service.run_context("context_1", actor)
    runtime.set_run_status_for_test("run_1", status="COMPLETED")

    with pytest.raises(ContextCollabVersionConflict, match="stale writer"):
        await service.refresh_run("context_1", actor)
