from __future__ import annotations

from contextlib import contextmanager

import pytest

from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoAcceptanceModel,
    TodoModel,
)
from agentclaw.community.core.repository.implementations.task.todos.todo_repository import (
    TodoRepository,
)
from agentclaw.community.core.task.todos.errors import (
    TodoNotFoundError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.models import AcceptanceVerdict, TodoStatus
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def _create(repo: TodoRepository, **overrides):
    values = {
        "space_id": 7,
        "title": "Build workbench",
        "description": "Implement Todo CRUD",
        "bot_id": "bot_1",
        "bot_name": "Bot One",
        "created_by": "user_1",
    }
    values.update(overrides)
    return repo.create_todo(**values)


def _set_status(db, todo_id: str, status: TodoStatus, **fields) -> None:
    with db.orm_session() as session:
        values = {"status": status.value, **fields}
        session.query(TodoModel).filter(TodoModel.todo_id == todo_id).update(values)


def test_create_and_get_todo(db):
    repo = TodoRepository(db)

    created = _create(repo)
    loaded = repo.get_todo(todo_id=created.todo_id)

    assert created.todo_id.startswith("todo_")
    assert loaded == created
    assert loaded.status is TodoStatus.DRAFT
    assert loaded.collab_context_id is None
    assert loaded.created_at is not None
    assert loaded.updated_at is not None


def test_start_if_draft_sets_context_and_running_once(db):
    repo = TodoRepository(db)
    created = _create(repo)

    started = repo.start_if_draft(
        todo_id=created.todo_id, collab_context_id="context_1"
    )

    assert started.status is TodoStatus.IN_PROGRESS
    assert started.collab_context_id == "context_1"

    with pytest.raises(TodoStateConflictError):
        repo.start_if_draft(todo_id=created.todo_id, collab_context_id="context_2")

    assert repo.get_todo(todo_id=created.todo_id).collab_context_id == "context_1"


def test_list_todos_filters_searches_and_paginates(db):
    repo = TodoRepository(db)
    _create(repo, title="Alpha", description="first", bot_name="Research Bot")
    second = _create(repo, title="Beta", description="alpha in description", bot_name="Writer")
    _create(repo, space_id=8, title="Alpha elsewhere")

    page = repo.list_todos(
        space_id=7,
        keyword="alpha",
        status=TodoStatus.DRAFT,
        offset=1,
        limit=1,
    )

    assert page.total == 2
    assert page.items == (repo.get_todo(todo_id=second.todo_id),)


def test_update_draft_replaces_editable_fields(db):
    repo = TodoRepository(db)
    created = _create(repo)

    updated = repo.update_draft(
        todo_id=created.todo_id,
        title="Changed",
        description="New detail",
        bot_id="bot_2",
        bot_name="Bot Two",
    )

    assert updated.title == "Changed"
    assert updated.description == "New detail"
    assert updated.bot_id == "bot_2"
    assert updated.bot_name == "Bot Two"


def test_update_non_draft_is_rejected_without_modifying_row(db):
    repo = TodoRepository(db)
    created = _create(repo)
    _set_status(db, created.todo_id, TodoStatus.IN_PROGRESS)

    with pytest.raises(TodoStateConflictError):
        repo.update_draft(
            todo_id=created.todo_id,
            title="Changed",
            description="New detail",
            bot_id="bot_2",
            bot_name="Bot Two",
        )

    assert repo.get_todo(todo_id=created.todo_id).title == "Build workbench"


def test_interaction_boundary_transitions_are_state_guarded(db):
    repo = TodoRepository(db)
    created = _create(repo)

    with pytest.raises(TodoStateConflictError):
        repo.mark_running_if_pending_acceptance(todo_id=created.todo_id)

    _set_status(db, created.todo_id, TodoStatus.PENDING_ACCEPTANCE)
    resumed = repo.mark_running_if_pending_acceptance(todo_id=created.todo_id)
    assert resumed.status is TodoStatus.IN_PROGRESS

    with pytest.raises(TodoStateConflictError):
        repo.mark_running_if_pending_acceptance(todo_id=created.todo_id)


def test_completion_projection_marks_pending_only_while_running(db):
    repo = TodoRepository(db)
    created = _create(repo)
    _set_status(db, created.todo_id, TodoStatus.IN_PROGRESS)

    pending = repo.mark_pending_acceptance_if_in_progress(
        todo_id=created.todo_id
    )
    assert pending.status is TodoStatus.PENDING_ACCEPTANCE

    completed = repo.mark_pending_acceptance_if_in_progress(
        todo_id=created.todo_id
    )
    assert completed.status is TodoStatus.PENDING_ACCEPTANCE


def test_repository_isolates_tenants(db):
    repo = TodoRepository(db)
    with avernet_tenant_scope("tenant-a"):
        created = _create(repo)

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(TodoNotFoundError):
            repo.get_todo(todo_id=created.todo_id)
        assert repo.list_todos(
            space_id=7, keyword=None, status=None, offset=0, limit=10
        ).total == 0


def test_repository_isolates_environments(db, monkeypatch):
    repo = TodoRepository(db)
    monkeypatch.setenv("SERVER_ENV", "pre")
    created = _create(repo)

    monkeypatch.setenv("SERVER_ENV", "prod")
    with pytest.raises(TodoNotFoundError):
        repo.get_todo(todo_id=created.todo_id)


@pytest.mark.parametrize(
    "verdict,target_status",
    [
        (AcceptanceVerdict.APPROVED, TodoStatus.COMPLETED),
        (AcceptanceVerdict.REJECTED, TodoStatus.IN_PROGRESS),
    ],
)
def test_acceptance_records_decision_and_changes_status_atomically(
    db, verdict, target_status
):
    repo = TodoRepository(db)
    created = _create(repo)
    _set_status(db, created.todo_id, TodoStatus.PENDING_ACCEPTANCE)

    updated = repo.accept_if_pending(
        todo_id=created.todo_id,
        verdict=verdict,
        message="reviewed",
        author_id="user_1",
    )

    assert updated.status is target_status
    with db.orm_session() as session:
        decisions = session.query(TodoAcceptanceModel).all()
        assert len(decisions) == 1
        assert decisions[0].todo_id == created.todo_id
        assert decisions[0].verdict == verdict.value


def test_repeated_acceptance_allows_only_first_decision(db):
    repo = TodoRepository(db)
    created = _create(repo)
    _set_status(db, created.todo_id, TodoStatus.PENDING_ACCEPTANCE)

    repo.accept_if_pending(
        todo_id=created.todo_id,
        verdict=AcceptanceVerdict.APPROVED,
        message="ok",
        author_id="user_1",
    )
    with pytest.raises(TodoStateConflictError):
        repo.accept_if_pending(
            todo_id=created.todo_id,
            verdict=AcceptanceVerdict.REJECTED,
            message="retry",
            author_id="user_1",
        )

    with db.orm_session() as session:
        assert session.query(TodoAcceptanceModel).count() == 1


def test_unknown_todo_raises_not_found_for_addressed_operations(db):
    repo = TodoRepository(db)

    with pytest.raises(TodoNotFoundError):
        repo.get_todo(todo_id="todo_missing")
    with pytest.raises(TodoNotFoundError):
        repo.start_if_draft(todo_id="todo_missing", collab_context_id="context")
    with pytest.raises(TodoNotFoundError):
        repo.mark_running_if_pending_acceptance(todo_id="todo_missing")


def test_database_write_failure_is_not_swallowed(db):
    repo = TodoRepository(db)

    class BrokenSession:
        def add(self, _record):
            raise RuntimeError("write failed")

    @contextmanager
    def broken_transaction():
        yield BrokenSession()

    repo._transaction = broken_transaction  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="write failed"):
        _create(repo)
