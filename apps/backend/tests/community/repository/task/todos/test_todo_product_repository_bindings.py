from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoModel,
    TodoSourceBindingModel,
    TodoStageInstanceModel,
)
from agentclaw.community.core.repository.implementations.task.todos.todo_repository import (
    TodoRepository,
)
from agentclaw.community.core.task.todos.errors import (
    TodoSourceAlreadyImportedError,
    TodoStateConflictError,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.source_models import TodoSourceInput
from agentclaw.community.core.task.todos.stage_models import (
    TodoStageRecord,
    TodoStageStatus,
)

NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)


def _source(**overrides: object) -> TodoSourceInput:
    values: dict[str, object] = {
        "source_type": "dima",
        "container_id": "P1",
        "item_id": "DIM-1",
        "url": "https://dima.example/DIM-1",
        "updated_at": "2026-11-27T00:00:00Z",
        "metadata": {"processor_name": "Owner One"},
    }
    values.update(overrides)
    return TodoSourceInput(**values)  # type: ignore[arg-type]


def _stage(todo_id: str) -> TodoStageRecord:
    return TodoStageRecord(
        stage_instance_id="stage_1",
        todo_id=todo_id,
        template_version_id="todo_tplv_1",
        stage_key="stage_1",
        stage_order=0,
        stage_name="Requirement analysis",
        purpose="Analyze requirement and acceptance",
        stage_kind="bot",
        executor_binding_key="executor",
        executor_subject_type="bot",
        executor_subject_id="bot_1",
        executor_subject_name="Coding Bot",
        owner_binding_key="owner",
        owner_user_id="owner_1",
        owner_user_name="Owner One",
        completion_gate_json='{"required_artifacts":[]}',
        status=TodoStageStatus.NOT_STARTED,
        collab_context_id=None,
        pending_action_kind=None,
        state_reason=None,
        requested_action=None,
        started_at=None,
        completed_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _todo(todo_id: str) -> TodoRecord:
    return TodoRecord(
        todo_id=todo_id,
        space_id=7,
        title="Implement Todo",
        description="Implement and verify",
        bot_id="bot_1",
        bot_name="Coding Bot",
        status=TodoStatus.NOT_STARTED,
        collab_context_id=None,
        created_by="creator_1",
        created_at=NOW,
        updated_at=NOW,
        template_id="todo_tpl_1",
        template_version_id="todo_tplv_1",
        template_name_snapshot="Coding delivery",
        current_stage_instance_id="stage_1",
    )


def _aggregate(repo: TodoRepository, *, todo_id="todo_1", source=None):
    return repo.create_todo_with_stages(
        record=_todo(todo_id),
        participant_bindings=(),
        stages=(_stage(todo_id),),
        source=source,
    )


def test_product_todo_aggregate_persists_source_in_the_same_transaction(db) -> None:
    repo = TodoRepository(db)
    source = _source()

    created = _aggregate(repo, source=source)

    assert created.product_collet_id is None
    with db.orm_session() as session:
        assert session.query(TodoModel).filter_by(todo_id=created.todo_id).one()
        binding = session.query(TodoSourceBindingModel).one()
        assert binding.todo_id == created.todo_id
        assert binding.source_item_id == "DIM-1"
        assert session.query(TodoStageInstanceModel).count() == 1


def test_product_todo_aggregate_duplicate_source_returns_existing_todo(db) -> None:
    repo = TodoRepository(db)
    existing = _aggregate(repo, todo_id="todo_existing", source=_source())

    with pytest.raises(TodoSourceAlreadyImportedError) as error:
        _aggregate(repo, todo_id="todo_duplicate", source=_source(container_id="P2"))

    assert error.value.existing_todo_id == existing.todo_id


def test_product_todo_aggregate_stage_failure_rolls_back_todo_and_source(db, monkeypatch) -> None:
    repo = TodoRepository(db)

    def broken_stage(**kwargs):
        raise RuntimeError("stage write failed")

    monkeypatch.setattr(repo, "_stage_model", broken_stage)

    with pytest.raises(RuntimeError, match="stage write failed"):
        _aggregate(repo, source=_source())

    with db.orm_session() as session:
        assert session.query(TodoModel).count() == 0
        assert session.query(TodoSourceBindingModel).count() == 0
        assert session.query(TodoStageInstanceModel).count() == 0


def test_bind_product_collet_uses_compare_and_set_semantics(db) -> None:
    repo = TodoRepository(db)
    created = _aggregate(repo)

    bound = repo.bind_product_collet(
        todo_id=created.todo_id,
        product_collet_id=11,
    )
    replay = repo.bind_product_collet(
        todo_id=created.todo_id,
        product_collet_id=11,
    )
    assert bound == replay
    with pytest.raises(TodoStateConflictError, match="different Product Collet"):
        repo.bind_product_collet(
            todo_id=created.todo_id,
            product_collet_id=22,
        )
