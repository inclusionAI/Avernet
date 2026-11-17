"""Full L4 service with lower-only read facts; no application/L5/L6 assembly."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
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
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service import (
    ContextService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    EventAppendCommand,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.service_ports import (
    CollaborationPolicy,
    CollaborationStore,
    WorkLifecycle,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
BOT = ActorContext(PrincipalRef("bot", "worker"), "a2a")
CID = "collet_l4"
NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


@dataclass(slots=True)
class LowerReadState:
    collet: Collet
    task: Task | None
    tasks: tuple[Task, ...]
    events: list[EventRecord]

    @property
    def current_snapshot(self) -> CurrentSnapshot | None:
        return project_current_snapshot(self.task, None)


def event(event_id="e1", *, kind="task.created", primitive="Task", parent=None):
    return EventRecord(
        collab_context_id=CID,
        event_id=event_id,
        parent_event=parent,
        event_kind=kind,
        primitive_type=primitive,
        actor_ref=OWNER.actor,
        visibility="public",
        payload={},
        context_version_after=11,
        occurred_at=NOW,
    )


def message(*, version=11, visibility="public"):
    return EventAppendCommand(
        version,
        "user_interaction.posted",
        "Message",
        BOT.actor,
        None,
        visibility,
        {"content": "Status note"},
    )


@pytest.fixture
def runtime():
    collet = Collet(CID, OWNER.actor, ColletState.ACTIVE, SpaceRef("space", "test"), 11)
    task = Task(
        task_id="task_l4",
        collet_ref=CID,
        version=7,
        objective="Deliver work",
        status="created",
        principal_ref=OWNER.actor,
        executor_refs=(AgentRef("bot", "worker"),),
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="e1",
        last_event_ref="e1",
    )
    state = LowerReadState(collet, task, (task,), [event()])
    store = Mock(spec=CollaborationStore)
    store.get_current_status.return_value = state
    store.append_event.return_value = SimpleNamespace(
        event=event("e2", kind="task.started"),
        collet=replace(collet, version=12),
        task=replace(task, version=8, status="in_progress", last_event_ref="e2"),
    )
    policy = Mock(spec=CollaborationPolicy)
    policy.validate_append.return_value = WorkLifecycle("task_commission_goal", 1)
    policy.filter_visible_events.side_effect = lambda record, context: list(
        record.events
    )
    return ContextService(store, policy), store, policy, state


def test_start_owns_resolution_and_commit_using_only_lower_state(runtime):
    service, store, policy, state = runtime
    assert not hasattr(state, "creation_facts")
    assert not hasattr(state, "product_instance")
    result = service.start(CID, WorkStartCommand(11, "Begin"), BOT)
    store.get_current_status.assert_called_once_with(CID)
    policy.check_append.assert_called_once()
    policy.validate_append.assert_called_once()
    checked_state, intent, actor = policy.check_append.call_args.args
    assert checked_state is state
    assert actor is BOT
    policy.validate_append.assert_called_once_with(state, intent, BOT)
    assert (intent.event_kind, intent.primitive_type, intent.actor_ref) == (
        "task.started",
        "Task",
        BOT.actor,
    )
    assert intent.parent_event is None and intent.visibility == "public"
    assert intent.payload == {"note": "Begin"}
    store.append_event.assert_called_once()
    cid, request = store.append_event.call_args.args
    assert cid == CID
    assert request.expected_version == 11
    assert request.task_update.task_ref == "task_l4"
    assert request.task_update.expected_version == 7
    assert request.task_update.status == "in_progress"
    assert (result.context_version, result.current_status, result.event_id) == (
        12,
        "in_progress",
        "e2",
    )
    assert result.accepted


def test_message_append_never_acquires_work_update(runtime):
    service, store, policy, state = runtime
    policy.validate_append.return_value = WorkLifecycle(None)
    store.append_event.return_value = SimpleNamespace(
        event=event("e2", kind="user_interaction.posted", primitive="Message"),
        collet=replace(state.collet, version=12),
        task=state.task,
    )
    result = service.append_event(CID, message(), BOT)
    policy.check_append.assert_called_once_with(state, message(), BOT)
    policy.validate_append.assert_called_once_with(state, message(), BOT)
    assert store.append_event.call_args.args[1].task_update is None
    assert result.context_version == 12 and result.current_status == "created"


def test_schema_rejection_precedes_g1_and_never_writes(runtime):
    service, store, policy, _ = runtime
    with pytest.raises(ContextCollabValidationError):
        service.append_event(CID, message(visibility="invalid"), BOT)
    policy.check_append.assert_not_called()
    policy.validate_append.assert_not_called()
    store.append_event.assert_not_called()


def test_g1_rejection_precedes_revision_and_contract_validation(runtime):
    service, store, policy, _ = runtime
    policy.check_append.side_effect = ContextCollabValidationError("admission denied")
    with pytest.raises(ContextCollabValidationError, match="admission denied"):
        service.start(CID, WorkStartCommand(10), BOT)
    policy.validate_append.assert_not_called()
    store.append_event.assert_not_called()


def test_stale_revision_precedes_contract_checks(runtime):
    service, store, policy, state = runtime
    with pytest.raises(ContextCollabVersionConflict):
        service.append_event(CID, message(version=10), BOT)
    policy.check_append.assert_called_once_with(state, message(version=10), BOT)
    policy.validate_append.assert_not_called()
    store.append_event.assert_not_called()


def test_product_contract_rejection_does_not_prepare_or_commit_work(runtime):
    service, store, policy, _ = runtime
    policy.validate_append.side_effect = ContextCollabValidationError(
        "contract rejected"
    )
    with pytest.raises(ContextCollabValidationError, match="contract rejected"):
        service.start(CID, WorkStartCommand(11), BOT)
    store.append_event.assert_not_called()


@pytest.mark.parametrize(
    "fault", ["no_task", "no_machine", "missing_pinned_machine", "terminal"]
)
def test_work_requires_task_and_matching_pinned_transition(runtime, fault):
    service, store, policy, state = runtime
    if fault == "no_task":
        state.task, state.tasks = None, ()
    elif fault == "no_machine":
        policy.validate_append.return_value = WorkLifecycle(None)
    elif fault == "missing_pinned_machine":
        policy.validate_append.return_value = WorkLifecycle(
            "task_commission_goal", 99999
        )
    else:
        state.task = replace(state.task, status="done")
        state.tasks = (state.task,)
    with pytest.raises(ContextCollabValidationError):
        service.start(CID, WorkStartCommand(11), BOT)
    store.append_event.assert_not_called()


@pytest.mark.parametrize("error", [ContextCollabVersionConflict, RuntimeError])
def test_work_commit_propagates_cas_and_write_failure(runtime, error):
    service, store, policy, _ = runtime
    store.append_event.side_effect = error("write rejected")
    with pytest.raises(error, match="write rejected"):
        service.start(CID, WorkStartCommand(11), BOT)
    policy.validate_append.assert_called_once()
    store.append_event.assert_called_once()


@pytest.mark.parametrize(
    "operation", ["start", "append_event", "get_status", "get_events"]
)
def test_missing_collet_rejected_before_policy_or_write(runtime, operation):
    service, store, policy, _ = runtime
    store.get_current_status.return_value = None
    with pytest.raises(ContextCollabNotFoundError):
        if operation == "start":
            service.start(CID, WorkStartCommand(11), BOT)
        elif operation == "append_event":
            service.append_event(CID, message(), BOT)
        elif operation == "get_status":
            service.get_status(CID, trunk_events=False, context=OWNER)
        else:
            service.get_events(CID, context=OWNER)
    assert policy.mock_calls == []
    store.append_event.assert_not_called()


def test_queries_apply_g1_before_g3_and_do_not_write(runtime):
    service, store, policy, state = runtime
    state.events.append(event("private_e2", kind="task.started"))
    state.task = replace(state.task, last_event_ref="private_e2", status="in_progress")
    state.tasks = (state.task,)
    policy.filter_visible_events.side_effect = None
    policy.filter_visible_events.return_value = state.events[:1]
    result = service.get_status(CID, trunk_events=False, context=OWNER)
    assert result.current_event is None
    assert result.current_snapshot is None  # latest Task evidence is not visible
    assert result.task_progress.total_events == 1
    assert [row["event_id"] for row in result.task_progress.events] == ["e1"]
    assert service.get_events(CID, context=OWNER) == state.events[:1]
    assert policy.filter_visible_events.call_count == 2
    policy.filter_visible_events.assert_called_with(state, OWNER)
    store.create_l4_collet.assert_not_called()
    store.append_event.assert_not_called()
    policy.check_append.assert_not_called()
    policy.validate_append.assert_not_called()


def test_trunk_only_filters_returned_events_not_task_progress(runtime):
    service, store, policy, state = runtime
    state.events += [
        event(
            "branch", kind="user_interaction.posted", primitive="Message", parent="e1"
        ),
        event(
            "current", kind="user_interaction.posted", primitive="Message", parent="e1"
        ),
    ]
    full = service.get_status(CID, trunk_events=False, context=OWNER)
    trunk = service.get_status(CID, trunk_events=True, context=OWNER)
    assert full.task_progress.total_events == 3
    assert trunk.task_progress.total_events == 2
    assert [row["event_id"] for row in trunk.task_progress.events] == ["e1", "current"]
    assert full.current_snapshot == trunk.current_snapshot
    assert full.current_event == trunk.current_event
    store.append_event.assert_not_called()


def test_partial_row_allows_message_and_read_but_not_work(runtime):
    service, store, policy, state = runtime
    state.task, state.tasks = None, ()
    policy.validate_append.return_value = WorkLifecycle(None)
    store.append_event.return_value = SimpleNamespace(
        event=event("e2", kind="user_interaction.posted", primitive="Message"),
        collet=replace(state.collet, version=12),
        task=None,
    )
    result = service.append_event(CID, message(), BOT)
    assert result.current_status is None
    assert store.append_event.call_args.args[1].task_update is None
    assert (
        service.get_status(CID, trunk_events=False, context=OWNER).current_snapshot
        is None
    )
    assert service.get_events(CID, context=OWNER) == state.events
