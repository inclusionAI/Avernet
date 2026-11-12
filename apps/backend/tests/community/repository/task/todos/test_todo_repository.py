from __future__ import annotations

from contextlib import contextmanager

import pytest

from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoAcceptanceModel,
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
        "description": "Implement automatic Todo execution",
        "bot_id": "bot_1",
        "bot_name": "Bot One",
        "created_by": "user_1",
    }
    values.update(overrides)
    return repo.create_todo(**values)


def _transition(
    repo: TodoRepository,
    todo_id: str,
    expected: TodoStatus,
    target: TodoStatus,
):
    return repo.transition_status(
        todo_id=todo_id,
        expected_status=expected,
        new_status=target,
    )


def test_create_and_get_todo_defaults_to_pending(db):
    repo = TodoRepository(db)

    created = _create(repo)
    loaded = repo.get_todo(todo_id=created.todo_id)

    assert created.todo_id.startswith("todo_")
    assert loaded == created
    assert loaded.status is TodoStatus.PENDING
    assert loaded.collab_context_id is None


def test_list_todos_filters_status_search_bot_and_paginates_in_database(db):
    repo = TodoRepository(db)
    first = _create(repo, title="Alpha", bot_name="Research Bot")
    second = _create(
        repo,
        title="Beta",
        description="alpha in description",
        bot_id="bot_2",
        bot_name="Writer",
    )
    _transition(repo, second.todo_id, TodoStatus.PENDING, TodoStatus.IN_PROGRESS)
    _create(repo, title="Gamma", description="alpha", bot_id="bot_3")
    _create(repo, space_id=8, title="Alpha elsewhere")

    page = repo.list_todos(
        space_id=7,
        keyword="alpha",
        status=TodoStatus.PENDING,
        bot_id=None,
        page=1,
        page_size=1,
    )

    assert page.total == 2
    assert page.items == (repo.get_todo(todo_id=first.todo_id),)


def test_list_todos_by_status_is_oldest_first_and_limited(db):
    repo = TodoRepository(db)
    first = _create(repo, title="first")
    second = _create(repo, title="second")
    _create(repo, title="third")
    _transition(repo, second.todo_id, TodoStatus.PENDING, TodoStatus.IN_PROGRESS)

    pending = repo.list_todos_by_status(status=TodoStatus.PENDING, limit=1)
    running = repo.list_todos_by_status(status=TodoStatus.IN_PROGRESS, limit=10)

    assert [todo.todo_id for todo in pending] == [first.todo_id]
    assert [todo.todo_id for todo in running] == [second.todo_id]


def test_bind_context_if_pending_attaches_once_without_changing_status(db):
    repo = TodoRepository(db)
    created = _create(repo)

    bound = repo.bind_context_if_pending(
        todo_id=created.todo_id,
        collab_context_id="context_1",
    )

    assert bound.status is TodoStatus.PENDING
    assert bound.collab_context_id == "context_1"
    with pytest.raises(TodoStateConflictError, match="already has"):
        repo.bind_context_if_pending(
            todo_id=created.todo_id,
            collab_context_id="context_2",
        )


def test_bind_context_requires_pending_and_non_empty_id(db):
    repo = TodoRepository(db)
    created = _create(repo)

    with pytest.raises(TodoStateConflictError, match="required"):
        repo.bind_context_if_pending(todo_id=created.todo_id, collab_context_id="")

    _transition(repo, created.todo_id, TodoStatus.PENDING, TodoStatus.IN_PROGRESS)
    with pytest.raises(TodoStateConflictError, match="pending"):
        repo.bind_context_if_pending(
            todo_id=created.todo_id,
            collab_context_id="context_1",
        )


def test_transition_status_requires_expected_status(db):
    repo = TodoRepository(db)
    created = _create(repo)

    running = _transition(
        repo,
        created.todo_id,
        TodoStatus.PENDING,
        TodoStatus.IN_PROGRESS,
    )

    assert running.status is TodoStatus.IN_PROGRESS
    with pytest.raises(TodoStateConflictError, match="expected pending"):
        _transition(
            repo,
            created.todo_id,
            TodoStatus.PENDING,
            TodoStatus.IN_PROGRESS,
        )


def test_repository_isolates_tenants_for_reads_and_scheduler_scans(db):
    repo = TodoRepository(db)
    with avernet_tenant_scope("tenant-a"):
        created = _create(repo)

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(TodoNotFoundError):
            repo.get_todo(todo_id=created.todo_id)
        assert repo.list_todos_by_status(status=TodoStatus.PENDING, limit=10) == ()
        assert (
            repo.list_todos(
                space_id=7,
                keyword=None,
                status=None,
                bot_id=None,
                page=1,
                page_size=20,
            ).total
            == 0
        )


def test_repository_isolates_environments(db, monkeypatch):
    repo = TodoRepository(db)
    monkeypatch.setenv("SERVER_ENV", "pre")
    created = _create(repo)

    monkeypatch.setenv("SERVER_ENV", "prod")
    with pytest.raises(TodoNotFoundError):
        repo.get_todo(todo_id=created.todo_id)
    assert repo.list_todos_by_status(status=TodoStatus.PENDING, limit=10) == ()


@pytest.mark.parametrize(
    "verdict,target_status",
    [
        (AcceptanceVerdict.APPROVED, TodoStatus.COMPLETED),
        (AcceptanceVerdict.REJECTED, TodoStatus.PENDING_ACCEPTANCE),
    ],
)
def test_record_acceptance_requires_pending_acceptance_and_persists_decision(
    db, verdict, target_status
):
    repo = TodoRepository(db)
    created = _create(repo)
    _transition(repo, created.todo_id, TodoStatus.PENDING, TodoStatus.IN_PROGRESS)
    _transition(
        repo,
        created.todo_id,
        TodoStatus.IN_PROGRESS,
        TodoStatus.PENDING_ACCEPTANCE,
    )

    updated = repo.record_acceptance(
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


def test_record_acceptance_rejects_other_states_without_writing_history(db):
    repo = TodoRepository(db)
    created = _create(repo)

    with pytest.raises(TodoStateConflictError, match="pending acceptance"):
        repo.record_acceptance(
            todo_id=created.todo_id,
            verdict=AcceptanceVerdict.APPROVED,
            message="ok",
            author_id="user_1",
        )

    with db.orm_session() as session:
        assert session.query(TodoAcceptanceModel).count() == 0


def test_unknown_todo_raises_not_found_for_addressed_operations(db):
    repo = TodoRepository(db)

    with pytest.raises(TodoNotFoundError):
        repo.get_todo(todo_id="todo_missing")
    with pytest.raises(TodoNotFoundError):
        repo.bind_context_if_pending(
            todo_id="todo_missing",
            collab_context_id="context",
        )
    with pytest.raises(TodoNotFoundError):
        repo.transition_status(
            todo_id="todo_missing",
            expected_status=TodoStatus.PENDING,
            new_status=TodoStatus.IN_PROGRESS,
        )
    with pytest.raises(TodoNotFoundError):
        repo.record_acceptance(
            todo_id="todo_missing",
            verdict=AcceptanceVerdict.APPROVED,
            message="",
            author_id="user_1",
        )


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
