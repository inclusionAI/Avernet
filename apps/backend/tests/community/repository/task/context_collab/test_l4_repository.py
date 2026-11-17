"""Actual L4 SQL storage, independently usable without products or a catalog."""

import json
from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.collet_lifecycle import (
    ColletLifecycleService,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
    ColletCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
    ContextCollabTaskModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    ContextCollabRepositoryProtocol,
    CreatedColletRecord,
    PrimitiveReadState,
    PrimitiveRecordDecoder,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskUpdate,
)
from agentclaw.community.core.task.context_collab.persistence.collet_lifecycle_store import (
    InMemoryColletLifecycleStore,
)
from sqlalchemy import event as sql_event

CREATOR = PrincipalRef("human", "creator")
RESPONSIBLE = PrincipalRef("human", "responsible")
WORKERS = (AgentRef("bot", "worker-a"), AgentRef("bot", "worker-b"))
MODELS = (ContextCollabColletModel, ContextCollabTaskModel, ContextCollabEventModel)


def command():
    return ColletCreateCommand(SpaceRef("space", "test"), RESPONSIBLE, "Deliver work")


def message(version=1):
    return AppendEventRecordRequest(
        version,
        "user_interaction.posted",
        "Message",
        CREATOR,
        None,
        "public",
        {"content": "A note"},
    )


def stored_counts(db):
    with db.orm_session() as session:
        return tuple(session.query(model).count() for model in MODELS)


@pytest.fixture
def repository(db):
    return ContextCollabRepository(db, PrimitiveRecordDecoder())


def test_l4_storage_creates_and_reads_without_product_dependencies(repository, db):
    created = repository.create_l4_collet(command(), CREATOR)
    assert type(created) is CreatedColletRecord
    assert isinstance(repository, ContextCollabRepositoryProtocol)
    assert type(repository).__module__.endswith(
        "l4_context_primitives.persistence.repository"
    )
    assert ContextCollabRepositoryProtocol.__module__.endswith(
        "l4_context_primitives.persistence.records"
    )
    assert created.collet.initiator_ref == CREATOR
    assert created.task.principal_ref == RESPONSIBLE
    assert created.task.executor_refs == ()
    assert created.collet.scope == command().to_space
    state = repository.get_current_status(created.collet.collet_id)
    assert type(state) is PrimitiveReadState
    assert state.collet == created.collet
    assert state.task == created.task
    assert state.tasks == (created.task,)
    assert state.events == [created.initial_event]
    assert state.owner_principal_id == CREATOR.principal_id
    assert state.designated_executor_id == (None)
    assert not hasattr(state, "creation_facts")
    assert not hasattr(state, "product_instance")
    assert state.current_snapshot.method_kind is None
    facts = {
        "objective": "Deliver work",
        "executor_refs": [],
    }
    assert created.initial_event.payload == facts
    assert created.initial_event.actor_ref == CREATOR
    assert created.initial_event.task_ref == created.task.task_id
    assert created.initial_event.task_version_after == 1
    assert stored_counts(db) == (1, 1, 1)
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        assert row.root_task_ref == created.task.task_id
        assert row.current_snapshot is None
        assert json.loads(row.creation_facts) == facts


def test_primitive_repository_satisfies_a_lower_reader_consumer(repository):
    created = repository.create_l4_collet(command(), CREATOR)
    cid = created.collet.collet_id
    lifecycle = ColletLifecycleService(InMemoryColletLifecycleStore(), repository)
    bot = ActorContext(CREATOR, "api")
    assert lifecycle.suspend(cid, bot).new_state == ColletState.SUSPENDED
    stranger = ActorContext(PrincipalRef("human", "stranger"), "api")
    with pytest.raises(ContextCollabValidationError, match="principal or executor"):
        lifecycle.resume(cid, stranger)
    assert lifecycle.resume(cid, bot).new_state == ColletState.ACTIVE
    assert repository.get_current_status(cid).collet == created.collet


def test_creation_never_calls_the_read_decoder(db):
    class RejectDecoder:
        def validate(self, *_):
            pytest.fail("creation called a metadata validator")

        def decode(self, *_):
            pytest.fail("creation called a read decoder")

    created = ContextCollabRepository(db, RejectDecoder()).create_l4_collet(
        command(), CREATOR
    )
    assert created.task.objective == "Deliver work"
    assert stored_counts(db) == (1, 1, 1)


def test_read_passes_the_exact_sql_state_once_and_does_not_write(db, engine):
    reads, writes = [], []

    class RecordingDecoder(PrimitiveRecordDecoder):
        def decode(self, state, facts_json):
            reads.append((state, facts_json))
            return super().decode(state, facts_json)

    repository = ContextCollabRepository(db, RecordingDecoder())
    assert repository.get_current_status("missing") is None
    assert reads == []
    created = repository.create_l4_collet(command(), CREATOR)

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    sql_event.listen(engine, "before_cursor_execute", observe)
    try:
        state = repository.get_current_status(created.collet.collet_id)
    finally:
        sql_event.remove(engine, "before_cursor_execute", observe)
    assert len(reads) == 1
    assert reads[0][0] is state
    assert json.loads(reads[0][1]) == created.initial_event.payload
    assert writes == []


def test_message_and_work_preserve_independent_revisions_and_evidence(repository):
    created = repository.create_l4_collet(command(), CREATOR)
    cid = created.collet.collet_id
    saved = repository.append_event(cid, message())
    assert saved.collet.version == 2
    assert saved.task == created.task
    assert json.loads(saved.creation_facts_json) == created.initial_event.payload
    work = AppendEventRecordRequest(
        2,
        "task.started",
        "Task",
        CREATOR,
        saved.event.event_id,
        "public",
        {},
        TaskUpdate(created.task.task_id, 1, "in_progress"),
    )
    started = repository.append_event(cid, work)
    state = repository.get_current_status(cid)
    assert state.collet == started.collet
    assert state.task == started.task
    assert state.collet.version == 3
    assert state.task.version == 2 and state.task.status == "in_progress"
    assert state.task.executor_refs == ()
    assert state.task.last_event_ref == started.event.event_id
    assert state.events[-1].task_ref == created.task.task_id
    assert state.events[-1].task_version_after == 2
    assert repository.count_message_events([cid, "missing"]) == {cid: 1}
    before = state
    with pytest.raises(ContextCollabVersionConflict):
        repository.append_event(cid, message(version=2))
    with pytest.raises(ContextCollabVersionConflict):
        repository.append_event(cid, replace(work, expected_version=3))
    assert repository.get_current_status(cid) == before


@pytest.mark.parametrize("target", [ContextCollabTaskModel, ContextCollabEventModel])
def test_l4_sql_creation_failure_rolls_back_all_three_records(repository, db, target):
    def fail(*_):
        raise RuntimeError("injected SQL insert failure")

    sql_event.listen(target, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="injected SQL insert failure"):
            repository.create_l4_collet(command(), CREATOR)
    finally:
        sql_event.remove(target, "before_insert", fail)
    assert stored_counts(db) == (0, 0, 0)


@pytest.mark.parametrize(
    "target", ["ac_context_collab_collet", "ac_context_collab_event"]
)
def test_l4_work_write_failure_restores_task_collet_and_events(
    repository, db, engine, target
):
    created = repository.create_l4_collet(command(), CREATOR)
    cid = created.collet.collet_id
    before = repository.get_current_status(cid)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip().upper().startswith(("INSERT", "UPDATE"))
            and target in statement
        ):
            raise RuntimeError("injected SQL append failure")

    sql_event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected SQL append failure"):
            repository.append_event(
                cid,
                AppendEventRecordRequest(
                    1,
                    "task.started",
                    "Task",
                    CREATOR,
                    None,
                    "public",
                    {},
                    TaskUpdate(created.task.task_id, 1, "in_progress"),
                ),
            )
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail)
    assert repository.get_current_status(cid) == before
    assert stored_counts(db) == (1, 1, 1)


@pytest.mark.parametrize(
    "facts",
    [
        {},
        {"objective": "work", "executor_refs": None},
        {"objective": "work", "executor_refs": [], "profile_id": "not-l4"},
    ],
)
def test_lower_decoder_rejects_invalid_storage_on_read_and_before_append(
    repository, db, facts
):
    created = repository.create_l4_collet(command(), CREATOR)
    cid = created.collet.collet_id
    with db.orm_session() as session:
        session.query(ContextCollabColletModel).one().creation_facts = json.dumps(facts)
    with pytest.raises(ContextCollabValidationError):
        repository.get_current_status(cid)
    with pytest.raises(ContextCollabValidationError):
        repository.append_event(cid, message())
    assert stored_counts(db) == (1, 1, 1)
