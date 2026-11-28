"""Real L4 SQL persistence of Collet description/audit, CAS and legacy history."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest,
    ColletCreateCommand,
)
from tests.community.repository.task.context_collab.context_spec_test_support import (  # noqa: I001
    store_context_value,
    stored_context_value,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    PrimitiveRecordDecoder,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.repository import (
    ContextCollabRepository,
)
from sqlalchemy import event as sql_event

CREATOR = PrincipalRef("human", "creator")
RESPONSIBLE = PrincipalRef("human", "responsible")
REPORTER = PrincipalRef("bot", "worker")


@pytest.fixture
def repository(db):
    return ContextCollabRepository(db, PrimitiveRecordDecoder())


def create(repository):
    return repository.create_l4_collet(
        ColletCreateCommand(
            RESPONSIBLE,
            title="协作上下文",
            description="一次有界的交付与验收协作",
        ),
        CREATOR,
    )


def message():
    return AppendEventRecordRequest(
        1,
        "user_interaction.posted",
        "Message",
        REPORTER,
        None,
        "public",
        {"content": "进展说明"},
    )


def test_creation_persists_complete_metadata_separate_from_task_and_product_facts(
    repository,
    db,
):
    before = datetime.now(timezone.utc)
    record = create(repository)
    after = datetime.now(timezone.utc)
    collet = record.collet
    assert collet.initiator_ref == collet.created_by == collet.updated_by == CREATOR
    assert not hasattr(record, "task")
    assert before <= collet.created_at == collet.updated_at <= after
    assert collet.version == 1
    assert "schema_version" not in collet.to_dict()
    assert collet.title == "协作上下文"
    assert collet.description == "一次有界的交付与验收协作"
    state = repository.get_current_status(collet.collet_id)
    assert state.collet == collet
    assert state.collet.etag == collet.etag
    assert state.task is None and state.tasks == () and state.events == []
    assert not hasattr(collet, "objective") and not hasattr(collet, "executor_refs")
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        assert stored_context_value(row, "collet_metadata") == (
            collet.metadata.to_dict()
        )
        assert stored_context_value(row, "creation_facts") == {}
        assert stored_context_value(row, "root_task_ref") is None
        assert row.current_snapshot is None


def test_append_updates_audit_atomically_but_preserves_creation_and_task(
    repository,
    db,
):
    original = create(repository).collet
    original_state = repository.get_current_status(original.collet_id)
    saved = repository.append_event(original.collet_id, message())
    current = repository.get_current_status(original.collet_id).collet
    assert current == saved.collet
    assert saved.task == original_state.task
    assert current.version == original.version + 1
    assert current.etag != original.etag
    assert current.updated_by == REPORTER
    assert current.updated_at == saved.event.occurred_at >= original.updated_at
    assert current.created_at == original.created_at
    assert current.created_by == original.created_by
    assert (
        current.title == original.title and current.description == original.description
    )
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        assert (
            stored_context_value(row, "collet_metadata") == current.metadata.to_dict()
        )
    with pytest.raises(ContextCollabVersionConflict):
        repository.append_event(original.collet_id, message())
    assert repository.get_current_status(original.collet_id).collet == current


def test_read_preserves_audit_precision_and_does_not_use_shared_row_timestamps(
    repository,
    db,
    engine,
):
    record = create(repository)
    # Shared SQL row timestamps can change for reasons outside Collet authority.
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        row.gmt_create = datetime(2000, 1, 1)
        row.gmt_modified = datetime(2001, 1, 1)
    writes = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    sql_event.listen(engine, "before_cursor_execute", observe)
    try:
        readback = repository.get_current_status(record.collet.collet_id)
        repeated = repository.get_current_status(record.collet.collet_id)
    finally:
        sql_event.remove(engine, "before_cursor_execute", observe)
    assert readback == repeated
    assert readback.collet == record.collet
    assert readback.collet.etag == record.collet.etag
    assert writes == []


@pytest.mark.parametrize(
    "target", ["ac_context_collab_collet", "ac_context_collab_event"]
)
def test_append_failure_rolls_back_metadata_head_and_event(
    repository, db, engine, target
):
    record = create(repository)
    cid = record.collet.collet_id
    before = repository.get_current_status(cid)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip().upper().startswith(("INSERT", "UPDATE"))
            and target in statement
        ):
            raise RuntimeError("injected audit write failure")

    sql_event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected audit write failure"):
            repository.append_event(cid, message())
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail)
    assert repository.get_current_status(cid) == before
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        assert (
            stored_context_value(row, "collet_metadata")
            == before.collet.metadata.to_dict()
        )


def test_legacy_null_metadata_reads_and_appends_without_inventing_history(
    repository, db
):
    record = create(repository)
    cid = record.collet.collet_id
    with db.orm_session() as session:
        store_context_value(
            session.query(ContextCollabColletModel).one(), "collet_metadata", None
        )
    legacy = repository.get_current_status(cid).collet
    assert legacy.metadata is None
    assert legacy.created_at is None and legacy.created_by is None
    assert legacy.updated_at is None and legacy.updated_by is None
    assert set(legacy.to_dict()) == {
        "collet_id",
        "initiator_ref",
        "scope",
        "state",
        "version",
    }
    saved = repository.append_event(cid, message())
    assert saved.collet.metadata is None
    assert saved.collet == replace(
        legacy,
        version=2,
    )
    assert repository.get_current_status(cid).collet == saved.collet
    with db.orm_session() as session:
        assert (
            stored_context_value(
                session.query(ContextCollabColletModel).one(), "collet_metadata"
            )
            is None
        )


@pytest.mark.parametrize(
    "bad", ["{bad json", "null", "[]", "{}", '{"title":"partial"}']
)
def test_corrupt_or_partial_metadata_fails_read_and_append_without_writes(
    repository,
    db,
    bad,
):
    record = create(repository)
    cid = record.collet.collet_id
    with db.orm_session() as session:
        store_context_value(
            session.query(ContextCollabColletModel).one(), "collet_metadata", bad
        )
    with pytest.raises(ContextCollabValidationError, match="context_spec"):
        repository.get_current_status(cid)
    with pytest.raises(ContextCollabValidationError, match="context_spec"):
        repository.append_event(cid, message())
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        assert (
            row.context_version == 1
            and stored_context_value(row, "root_task_ref") is None
        )
        assert stored_context_value(row, "collet_metadata") == bad


def test_retired_schema_marker_reads_without_rewrite_and_is_removed_on_append(
    repository, db
):
    original = create(repository).collet
    stored = {**original.metadata.to_dict(), "schema_version": "1.1"}
    with db.orm_session() as session:
        store_context_value(
            session.query(ContextCollabColletModel).one(), "collet_metadata", stored
        )

    current = repository.get_current_status(original.collet_id).collet
    assert current == original
    assert "schema_version" not in current.to_dict()
    with db.orm_session() as session:
        assert (
            stored_context_value(
                session.query(ContextCollabColletModel).one(), "collet_metadata"
            )
            == stored
        )

    saved = repository.append_event(original.collet_id, message()).collet
    assert saved.created_at == original.created_at
    assert saved.created_by == original.created_by
    assert saved.version == original.version + 1
    with db.orm_session() as session:
        metadata = stored_context_value(
            session.query(ContextCollabColletModel).one(), "collet_metadata"
        )
        assert metadata == saved.metadata.to_dict()
        assert "schema_version" not in metadata


@pytest.mark.parametrize("value", [None, 1.1, "unknown"])
def test_unknown_retired_schema_marker_still_fails_without_writes(
    repository, db, value
):
    original = create(repository).collet
    stored = {**original.metadata.to_dict(), "schema_version": value}
    with db.orm_session() as session:
        store_context_value(
            session.query(ContextCollabColletModel).one(), "collet_metadata", stored
        )
    with pytest.raises(ContextCollabValidationError, match="context_spec"):
        repository.get_current_status(original.collet_id)
    with pytest.raises(ContextCollabValidationError, match="context_spec"):
        repository.append_event(original.collet_id, message())
    with db.orm_session() as session:
        row = session.query(ContextCollabColletModel).one()
        assert stored_context_value(row, "collet_metadata") == stored
        assert row.context_version == original.version


def test_collaboration_intent_persists_through_read_and_audit_append(repository, db):
    command = ColletCreateCommand(
        RESPONSIBLE,
        objective="实现计算器求和并提交 PR 草稿",
        acceptance_criteria="求和功能正确；单元测试通过；PR 草稿包含测试结果",
    )
    original = repository.create_l4_collet(command, CREATOR).collet
    metadata = original.metadata
    assert metadata.objective == command.objective
    assert metadata.acceptance_criteria == command.acceptance_criteria
    with db.orm_session() as session:
        stored = stored_context_value(
            session.query(ContextCollabColletModel).one(), "collet_metadata"
        )
        assert stored == metadata.to_dict()
    current = repository.get_current_status(original.collet_id).collet
    assert current.metadata == metadata
    saved = repository.append_event(original.collet_id, message()).collet
    assert saved.metadata.objective == metadata.objective
    assert saved.metadata.acceptance_criteria == metadata.acceptance_criteria
    assert saved.created_at == original.created_at
    assert saved.updated_by == REPORTER
    with db.orm_session() as session:
        stored = stored_context_value(
            session.query(ContextCollabColletModel).one(), "collet_metadata"
        )
        assert stored == saved.metadata.to_dict()


def test_stored_metadata_without_collaboration_intent_remains_readable(repository, db):
    original = create(repository).collet
    legacy = original.metadata.to_dict()
    del legacy["objective"], legacy["acceptance_criteria"]
    stored = legacy
    with db.orm_session() as session:
        store_context_value(
            session.query(ContextCollabColletModel).one(), "collet_metadata", stored
        )
    current = repository.get_current_status(original.collet_id).collet
    assert current.metadata.objective == current.metadata.acceptance_criteria == ""
    assert current.created_at == original.created_at
    with db.orm_session() as session:
        assert (
            stored_context_value(
                session.query(ContextCollabColletModel).one(), "collet_metadata"
            )
            == stored
        )
