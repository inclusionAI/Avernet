"""Todo runtime compatibility with canonical L4 authority and real SQL storage."""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_payload,
    decode_creation_facts,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_creation import (
    ContextCollabService,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_repository import (
    SqlProductProfileRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.todos.collaboration.context_runtime import (
    TodoContextRuntime,
)
from agentclaw.community.core.task.todos.collaboration.in_process import (
    InProcessTodoCollaborationAdapter,
)
from agentclaw.community.core.task.todos.collaboration.protocol import (
    TodoCollaborationContextRequest,
    TodoCollaborationError,
    TodoCollaborationEventRequest,
)
from tests.community.core.task.context_collab.executor_fakes import (
    RecordingContextCollabExecutor,
)


def _facts(**overrides):
    return creation_payload(
        **{
            "method_kind": "Single",
            "executor_ref": None,
            "objective": "Todo",
            "resource_refs": (),
            "product_kind": "team_todo",
            "workflow_kind": "team_todo_execution",
            **overrides,
        }
    )


def test_creation_owner_metadata_is_optional_and_round_trips():
    facts = _facts()
    assert "executor_owner_id" not in facts
    assert decode_creation_facts(facts) == facts
    owned = _facts(executor_owner_id="bot-owner")
    assert decode_creation_facts(owned) == owned


@pytest.mark.parametrize("owner", ["", " ", 123])
def test_creation_owner_metadata_rejects_invalid_values(owner):
    with pytest.raises(ContextCollabValidationError, match="executor_owner_id"):
        _facts(executor_owner_id=owner)
    with pytest.raises(ContextCollabValidationError, match="executor_owner_id"):
        decode_creation_facts({**_facts(), "executor_owner_id": owner})


@pytest.mark.asyncio
async def test_todo_create_run_complete_interact_resume_uses_l4_task_and_raw_events(db):
    profiles = SqlProductProfileRepository(db)
    repository = ContextCollabRepository(db, profiles)
    service = ContextCollabService(
        repository,
        InMemoryMethodStore(),
        product_catalog=profiles,
    )
    executor = RecordingContextCollabExecutor()
    runtime = TodoContextRuntime(service, repository, executor)
    adapter = InProcessTodoCollaborationAdapter(service, runtime)
    request = TodoCollaborationContextRequest(
        space_id="7",
        environment="test",
        principal_id="human-owner",
        product_kind="team_todo",
        workflow_kind="team_todo_execution",
        method_kind="Single",
        executor_id="bot_1",
        executor_owner_id="bot-owner",
        objective="Write the report",
        todo_id="todo_1",
    )
    created = adapter.create_context(request)
    context_id = created.collab_context_id
    actor = ActorContext(PrincipalRef("human", "human-owner"), "todo")
    assert created.status == "created"
    assert created.events[0].content == "Write the report"
    record = repository.get_current_status(context_id)
    assert record.creation_facts["executor_owner_id"] == "bot-owner"
    # Legacy combined creation binds inputs in the initial event, not Collet resources.
    assert record.collet.resources == ()
    assert not hasattr(record.collet, "current_snapshot")

    with pytest.raises(ContextCollabValidationError, match="Task principal"):
        await runtime.run_context(
            context_id, replace(actor, actor=PrincipalRef("human", "other"))
        )
    assert not executor.requests

    started = await adapter.run_context(
        collab_context_id=context_id, actor_id="human-owner"
    )
    assert executor.requests[0].executor_owner_id == "bot-owner"
    assert executor.requests[0].todo_id == "todo_1"
    assert started.status == "in_progress"
    assert started.events[-1].execution_id == "run_1"
    executor.set_run_status_for_test(
        "run_1", status="COMPLETED", session_id="session_1"
    )
    completed = await adapter.refresh_context_run(
        collab_context_id=context_id, actor_id="human-owner"
    )
    assert completed.status == "done"
    assert completed.events[-1].payload["run_id"] == "run_1"
    assert repository.get_current_status(context_id).task.status == "done"
    assert len(completed.events) == 3

    feedback = adapter.append_event(
        collab_context_id=context_id,
        request=TodoCollaborationEventRequest(
            expected_version=completed.context_version,
            event_kind="user_interaction.posted",
            primitive_type="Message",
            actor_id="human-owner",
            actor_type="human",
            parent_event=completed.current_event_id,
            visibility="workspace",
            payload={"content": "Please revise section 2"},
        ),
    )
    resumed = await adapter.resume_context(
        collab_context_id=context_id,
        actor_id="human-owner",
        triggered_by_event_id=feedback.event_id,
    )
    assert resumed.status == "in_progress"
    assert resumed.run_id == "run_2"
    assert resumed.events[-1].payload["session_id"] == "session_1"
    assert resumed.events[-1].payload["triggered_by_event_id"] == feedback.event_id
    assert executor.resumed_requests[0].message == "Please revise section 2"
    assert repository.get_current_status(context_id).task.version == 4
    # Non-principals cannot recover private workspace payload via Todo enrichment.
    outsider = replace(actor, actor=PrincipalRef("human", "other"))
    assert [
        event.event_id for event in service.get_events(context_id, context=outsider)
    ] == [created.events[0].event_id]
    with pytest.raises(TodoCollaborationError, match="current_snapshot.status"):
        adapter.get_context(collab_context_id=context_id, actor_id="other")
