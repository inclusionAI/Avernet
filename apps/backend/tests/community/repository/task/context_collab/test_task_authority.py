"""Task/Collet authority separation and legacy cutover against actual SQLite."""

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import event as sql_event

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskUpdate,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.models import (
    ContextCollabColletModel,
    ContextCollabEventModel,
    ContextCollabTaskModel,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    AppendEventRecordRequest,
    ContextCollabRepository,
    CreateContextRecordRequest,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    EventAppendCommand,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

OWNER = PrincipalRef("human", "u_task_owner")
EXECUTOR = AgentRef("bot", "bot_task_worker")


@pytest.fixture
def repository(db):
    return ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)


def _create(repository):
    return repository.create_context(
        CreateContextRecordRequest(
            context_spec=ContextSpec(
                space=SpaceRef("task-space", "prod"),
                principal_ref=OWNER,
                method_kind="Single",
                executor_ref=EXECUTOR,
                objective="交付可验收的报告",
                resource_refs=(),
                product_kind="team_todo",
                workflow_kind="team_todo_execution",
            ),
            initial_event=InitialEventDraft(
                "task.created", "Task", OWNER, {"objective": "交付可验收的报告"}
            ),
            completion_conditions=("成果提交后由发布人验收",),
        )
    )


def _message(repository, context_id, **overrides):
    record = repository.get_current_status(context_id)
    values = dict(
        expected_version=record.collet.version,
        event_kind="user_interaction.posted",
        primitive_type="Message",
        actor_ref=OWNER,
        parent_event=record.collet.current_event_id,
        visibility="public",
        payload={"message_kind": "user_interaction", "content": "补充说明"},
    )
    values.update(overrides)
    return repository.append_event(context_id, AppendEventRecordRequest(**values))


def _work(
    repository,
    context_id,
    *,
    kind="task.started",
    status="in_progress",
    payload=None,
    update=None,
):
    record = repository.get_current_status(context_id)
    return repository.append_event(
        context_id,
        AppendEventRecordRequest(
            expected_version=record.collet.version,
            event_kind=kind,
            primitive_type="Task",
            actor_ref=OWNER,
            parent_event=record.collet.current_event_id,
            visibility="public",
            payload=payload or {},
            task_update=update
            or TaskUpdate(record.task.task_id, record.task.version, status),
        ),
    )


def _storage(db):
    """Observe all three tables without using repository result projections."""
    with db.orm_session() as session:
        collets = [
            (
                r.collab_context_id,
                r.root_task_ref,
                r.context_version,
                r.current_event_id,
                r.current_snapshot,
            )
            for r in session.query(ContextCollabColletModel).order_by(
                ContextCollabColletModel.id
            )
        ]
        tasks = [
            (r.task_id, r.task_version, r.task_data)
            for r in session.query(ContextCollabTaskModel).order_by(
                ContextCollabTaskModel.id
            )
        ]
        events = [
            (
                r.event_id,
                r.task_ref,
                r.task_version_after,
                r.context_version_after,
                r.payload,
            )
            for r in session.query(ContextCollabEventModel).order_by(
                ContextCollabEventModel.id
            )
        ]
        return collets, tasks, events


def _make_legacy(db, repository, context_id):
    record = repository.get_current_status(context_id)
    snapshot = record.current_snapshot.to_dict()
    # Historic display counters must not become completed work-item counts.
    snapshot["progress"].update(percent=99, completed_count=999, active_count=999)
    with db.orm_session() as session:
        session.query(ContextCollabTaskModel).delete()
        row = session.query(ContextCollabColletModel).one()
        row.root_task_ref = None
        row.current_snapshot = json.dumps(snapshot)
        for event in session.query(ContextCollabEventModel):
            event.task_ref = None
            event.task_version_after = None


def test_independent_task_identity_revision_and_work_subject(repository, db):
    created = _create(repository)
    task = created.task
    assert task.task_id != created.collet.collet_id
    assert task.collet_ref == created.collet.collet_id
    assert task.parent_task_ref is None
    assert task.completion_conditions == ("成果提交后由发布人验收",)
    assert (
        task.created_event_ref == task.last_event_ref == created.initial_event.event_id
    )
    assert (
        created.initial_event.task_ref,
        created.initial_event.task_version_after,
    ) == (task.task_id, 1)

    message = _message(repository, task.collet_ref)
    assert message.collet.version == 2
    assert message.task == task
    assert message.event.task_ref is None
    assert message.event.task_version_after is None

    started = _work(repository, task.collet_ref)
    assert started.collet.version == 3
    assert started.task.version == 2
    assert started.task.status == "in_progress"
    assert started.task.last_event_ref == started.event.event_id
    assert (started.event.task_ref, started.event.task_version_after) == (
        task.task_id,
        2,
    )
    rows, tasks, events = _storage(db)
    assert rows[0][1] == task.task_id
    assert rows[0][-1] is None  # New writers never persist the compatibility view.
    assert tasks[0][1] == 2
    assert events[-1][2:4] == (2, 3)


def test_g4_uses_task_ignores_retired_snapshot_and_counts_work_items(
    repository, db, engine
):
    created = _create(repository)
    context_id = created.collet.collet_id
    with db.orm_session() as session:
        # Even invalid JSON in the retired column cannot become rooted Task authority.
        session.query(
            ContextCollabColletModel
        ).one().current_snapshot = "not authoritative JSON"
    service = ContextCollabService(
        repository=repository,
        method_store=InMemoryMethodStore(),
        product_catalog=PRODUCT_CATALOG,
    )
    owner = ActorContext(OWNER, "api")
    bot = ActorContext(PrincipalRef("bot", EXECUTOR.agent_id), "api")

    def advance(kind, payload=None, actor=bot):
        current = repository.get_current_status(context_id)
        return service.append_event(
            context_id,
            EventAppendCommand(
                expected_version=current.collet.version,
                event_kind=kind,
                primitive_type="Task",
                actor_ref=actor.actor,
                parent_event=current.collet.current_event_id,
                visibility="public",
                payload=payload or {},
            ),
            actor,
        )

    def progress():
        return service.get_status(
            context_id, trunk_events=False, context=owner
        ).current_snapshot["progress"]

    assert advance("task.started").current_status == "in_progress"
    for percent in (10, 60, 100):
        advance("task.progress.updated", {"percent": percent})
        assert progress()["completed_count"] == 0
        assert progress()["active_count"] == 1
        assert progress()["percent"] == percent
    assert repository.get_current_status(context_id).task.status == "in_progress"
    assert (
        advance("task.submitted", {"artifacts": ["artifact://report"]}).current_status
        == "reviewing"
    )
    assert progress()["completed_count"] == 0
    assert advance("task.accepted", actor=owner).current_status == "done"
    assert progress()["completed_count"] == 1
    assert progress()["percent"] == 100

    # A branch Message changes the displayed chain, never Task metrics/state.
    _message(repository, context_id, parent_event=created.initial_event.event_id)
    writes = []

    def record_writes(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            writes.append(statement)

    sql_event.listen(engine, "before_cursor_execute", record_writes)
    try:
        full = service.get_status(context_id, trunk_events=False, context=owner)
        trunk = service.get_status(context_id, trunk_events=True, context=owner)
    finally:
        sql_event.remove(engine, "before_cursor_execute", record_writes)
    assert full.current_snapshot == trunk.current_snapshot
    assert full.task_progress.total_events > trunk.task_progress.total_events
    assert full.current_snapshot["progress"]["completed_count"] == 1
    assert writes == []


@pytest.mark.parametrize("wrong_identity", [False, True])
def test_task_cas_conflict_does_not_write_any_table(repository, db, wrong_identity):
    created = _create(repository)
    before = _storage(db)
    update = TaskUpdate(
        "another-task" if wrong_identity else created.task.task_id, 2, "in_progress"
    )
    with pytest.raises(ContextCollabVersionConflict):
        _work(repository, created.collet.collet_id, update=update)
    assert _storage(db) == before


@pytest.mark.parametrize(
    "statement_prefix",
    ["UPDATE ac_context_collab_collet", "INSERT INTO ac_context_collab_event"],
)
@pytest.mark.parametrize("legacy", [False, True])
def test_storage_failure_rolls_back_task_event_and_collet(
    repository, db, engine, statement_prefix, legacy
):
    created = _create(repository)
    if legacy:
        _make_legacy(db, repository, created.collet.collet_id)
    before = _storage(db)

    def fail_write(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(statement_prefix):
            raise RuntimeError("injected SQL write failure")

    sql_event.listen(engine, "before_cursor_execute", fail_write)
    try:
        with pytest.raises(RuntimeError, match="injected SQL write failure"):
            _work(repository, created.collet.collet_id)
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_write)
    assert _storage(db) == before


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_task",
        "wrong_version",
        "wrong_identity",
        "missing_evidence",
        "wrong_subject",
        "orphan",
        "cycle",
    ],
)
def test_invalid_task_authority_fails_closed_without_snapshot_fallback(
    repository, db, corruption
):
    created = _create(repository)
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        row.current_snapshot = json.dumps(created.current_snapshot.to_dict())
        task_row = session.query(ContextCollabTaskModel).one()
        if corruption == "missing_task":
            session.delete(task_row)
        elif corruption == "wrong_version":
            task_row.task_version = 2
        elif corruption == "wrong_identity":
            data = json.loads(task_row.task_data)
            data["task_id"] = "different-task"
            task_row.task_data = json.dumps(data)
        elif corruption == "missing_evidence":
            task_row.task_data = json.dumps(
                replace(created.task, last_event_ref="missing-event").to_dict()
            )
        elif corruption == "wrong_subject":
            session.query(ContextCollabEventModel).one().task_ref = "different-task"
        elif corruption == "orphan":
            row.root_task_ref = None
        else:
            for child_id, parent_id in (("child-a", "child-b"), ("child-b", "child-a")):
                child = replace(
                    created.task, task_id=child_id, parent_task_ref=parent_id
                )
                session.add(
                    ContextCollabRepository._task_row(
                        child, now=datetime.now(timezone.utc)
                    )
                )
    before = _storage(db)
    with pytest.raises(ContextCollabValidationError):
        repository.get_current_status(created.collet.collet_id)
    assert _storage(db) == before


def test_legacy_read_is_deterministic_readonly_and_next_append_materializes(
    repository, db, engine
):
    created = _create(repository)
    context_id = created.collet.collet_id
    _work(repository, context_id)
    _work(repository, context_id, kind="task.progress.updated", payload={"percent": 60})
    _make_legacy(db, repository, context_id)
    before = _storage(db)
    first = repository.get_current_status(context_id)
    second = repository.get_current_status(context_id)
    assert first.task == second.task
    assert first.task.task_id.startswith("task_legacy_")
    assert first.task.version == 1
    assert first.task.legacy_context_version == 3
    assert first.task.created_event_ref is None
    assert first.task.last_event_ref is None
    assert first.task.reported_percent == 60
    assert _storage(db) == before

    message = _message(repository, context_id)
    assert message.task == first.task
    rows, tasks, events = _storage(db)
    assert rows[0][1] == first.task.task_id
    assert rows[0][-1] is None
    assert len(tasks) == 1
    assert events[:-1] == before[-1]  # No historical subject/revision rewriting.
    assert repository.get_current_status(context_id).task == first.task
    advanced = _work(
        repository,
        context_id,
        kind="task.submitted",
        status="reviewing",
        payload={"artifacts": ["artifact://report"]},
    )
    assert advanced.task.version == 2
    assert (
        advanced.task.legacy_context_version == 3
    )  # Original import baseline is pinned.
    assert (
        advanced.task.created_event_ref is None
    )  # Never fabricate a historic creation event.
    assert (
        repository.get_current_status(context_id).task.last_event_ref
        == advanced.event.event_id
    )


def test_partial_legacy_context_allows_message_but_not_work(repository, db):
    created = _create(repository)
    with db.orm_session() as session:
        session.query(ContextCollabTaskModel).delete()
        row = session.query(ContextCollabColletModel).one()
        row.root_task_ref = None
        row.current_snapshot = None
    assert repository.get_current_status(created.collet.collet_id).task is None
    message = _message(repository, created.collet.collet_id)
    assert message.task is None
    assert message.collet.version == 2
    before = _storage(db)
    with pytest.raises(ContextCollabValidationError, match="missing Task"):
        _work(
            repository,
            created.collet.collet_id,
            update=TaskUpdate("missing-task", 1, "in_progress"),
        )
    assert _storage(db) == before


@pytest.mark.parametrize(
    "table", ["ac_context_collab_task", "ac_context_collab_collet"]
)
def test_sql_cas_zero_match_rolls_back_all_authority(repository, db, engine, table):
    created = _create(repository)
    before = _storage(db)

    def force_conflict(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"UPDATE {table} "):
            return statement + " AND 0 = 1", parameters
        return statement, parameters

    sql_event.listen(engine, "before_cursor_execute", force_conflict, retval=True)
    try:
        with pytest.raises(ContextCollabVersionConflict):
            _work(repository, created.collet.collet_id)
    finally:
        sql_event.remove(engine, "before_cursor_execute", force_conflict)
    assert _storage(db) == before


@pytest.mark.parametrize(
    "corruption", ["wrong_percent", "message_evidence", "foreign_event"]
)
def test_task_evidence_is_checked_not_just_stored(repository, db, corruption):
    created = _create(repository)
    context_id = created.collet.collet_id
    _work(repository, context_id)
    reported = _work(
        repository, context_id, kind="task.progress.updated", payload={"percent": 60}
    )
    if corruption == "message_evidence":
        message = _message(repository, context_id)
        corrupted = replace(reported.task, progress_event_ref=message.event.event_id)
    elif corruption == "foreign_event":
        foreign = _create(repository)
        corrupted = replace(
            reported.task, last_event_ref=foreign.initial_event.event_id
        )
    else:
        corrupted = replace(reported.task, reported_percent=99)
    with db.orm_session() as session:
        row = (
            session.query(ContextCollabTaskModel)
            .filter(ContextCollabTaskModel.task_id == reported.task.task_id)
            .one()
        )
        row.task_data = json.dumps(corrupted.to_dict())
    before = _storage(db)
    with pytest.raises(ContextCollabValidationError):
        repository.get_current_status(context_id)
    assert _storage(db) == before
