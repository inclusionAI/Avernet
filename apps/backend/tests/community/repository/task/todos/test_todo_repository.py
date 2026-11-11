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
        "description": "Implement Todo create and run",
        "bot_id": "bot_1",
        "bot_name": "Bot One",
        "created_by": "user_1",
    }
    values.update(overrides)
    return repo.create_todo(**values)


def test_create_and_get_todo(db):
    repo = TodoRepository(db)

    created = _create(repo)
    loaded = repo.get_todo(todo_id=created.todo_id)

    assert created.todo_id.startswith("todo_")
    assert loaded == created
    assert loaded.status is TodoStatus.DRAFT
    assert loaded.collab_context_id is None


def test_bind_context_if_draft_attaches_context_without_changing_status(db):
    repo = TodoRepository(db)
    created = _create(repo)

    bound = repo.bind_context_if_draft(
        todo_id=created.todo_id,
        collab_context_id="context_1",
    )

    assert bound.status is TodoStatus.DRAFT
    assert bound.collab_context_id == "context_1"
    with pytest.raises(TodoStateConflictError, match="already has"):
        repo.bind_context_if_draft(
            todo_id=created.todo_id,
            collab_context_id="context_2",
        )


def test_bind_context_rejects_empty_context_id(db):
    repo = TodoRepository(db)
    created = _create(repo)

    with pytest.raises(TodoStateConflictError, match="required"):
        repo.bind_context_if_draft(
            todo_id=created.todo_id,
            collab_context_id="",
        )


def test_list_todo_candidates_filters_search_and_bot_without_pagination(db):
    repo = TodoRepository(db)
    _create(repo, title="Alpha", description="first", bot_name="Research Bot")
    second = _create(
        repo,
        title="Beta",
        description="alpha in description",
        bot_id="bot_2",
        bot_name="Writer",
    )
    _create(repo, title="Gamma", description="alpha", bot_id="bot_3")
    _create(repo, space_id=8, title="Alpha elsewhere")

    candidates = repo.list_todo_candidates(
        space_id=7,
        keyword="alpha",
        bot_id="bot_2",
    )

    assert candidates == (repo.get_todo(todo_id=second.todo_id),)


def test_repository_isolates_tenants(db):
    repo = TodoRepository(db)
    with avernet_tenant_scope("tenant-a"):
        created = _create(repo)

    with avernet_tenant_scope("tenant-b"):
        with pytest.raises(TodoNotFoundError):
            repo.get_todo(todo_id=created.todo_id)
        assert (
            repo.list_todo_candidates(
                space_id=7,
                keyword=None,
            )
            == ()
        )


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
        (AcceptanceVerdict.REJECTED, TodoStatus.PENDING_ACCEPTANCE),
    ],
)
def test_record_acceptance_persists_decision_and_a_side_status(
    db, verdict, target_status
):
    repo = TodoRepository(db)
    created = _create(repo)

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


def test_completed_todo_cannot_be_accepted_again(db):
    repo = TodoRepository(db)
    created = _create(repo)
    repo.record_acceptance(
        todo_id=created.todo_id,
        verdict=AcceptanceVerdict.APPROVED,
        message="ok",
        author_id="user_1",
    )

    with pytest.raises(TodoStateConflictError, match="Completed"):
        repo.record_acceptance(
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
        repo.bind_context_if_draft(
            todo_id="todo_missing",
            collab_context_id="context",
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
