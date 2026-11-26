from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agentclaw.community.core.repository.implementations.task.todos.todo_auto_import_repository import (
    TodoAutoImportRepository,
)
from agentclaw.community.core.task.todos.auto_import_models import (
    TodoAutoImportCheckpoint,
    TodoAutoImportProgressStatus,
    TodoAutoImportRuleStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoAutoImportCursorConflictError,
    TodoAutoImportRuleNotFoundError,
)

SOURCE_TIME = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)


def _create(repo: TodoAutoImportRepository, **overrides):
    values = {
        "space_id": 7,
        "source_container_ids": ("W1", "P1"),
        "filter_text": "只导入需要我处理的高优任务",
        "bot_id": "bot_1",
        "created_by": "user_1",
    }
    values.update(overrides)
    return repo.create_rule(**values)


def _checkpoint(**overrides) -> TodoAutoImportCheckpoint:
    values = {
        "source_container_id": "W1",
        "source_item_id": "2026112500000000001",
        "source_updated_at": SOURCE_TIME,
        "status": TodoAutoImportProgressStatus.IMPORTED,
        "todo_id": "todo_1",
        "detail": "created",
        "cursor_updated_at": SOURCE_TIME,
    }
    values.update(overrides)
    return TodoAutoImportCheckpoint(**values)


def test_management_is_owner_scoped_but_execution_reads_are_tenant_scoped(db) -> None:
    repo = TodoAutoImportRepository(db)
    created = _create(repo)
    other = _create(repo, created_by="user_2", space_id=8)

    assert created.rule_id.startswith("todo_air_")
    assert created.status is TodoAutoImportRuleStatus.ENABLED
    assert created.cursor_updated_at is None
    assert repo.get_rule(rule_id=created.rule_id) == created
    assert repo.get_rule(rule_id=other.rule_id) == other
    assert repo.get_owned_rule(rule_id=created.rule_id, created_by="user_1") == created
    assert repo.list_rules(created_by="user_1") == (created,)
    assert set(repo.list_active_rules()) == {created, other}
    assert repo.list_active_rules(space_id=7) == (created,)

    updated = repo.update_rule(
        rule_id=created.rule_id,
        created_by="user_1",
        source_container_ids=("P2",),
        filter_text="只导入缺陷",
        bot_id="bot_2",
        status=TodoAutoImportRuleStatus.DISABLED,
    )

    assert updated.source_container_ids == ("P2",)
    assert updated.filter_text == "只导入缺陷"
    assert updated.bot_id == "bot_2"
    assert updated.status is TodoAutoImportRuleStatus.DISABLED
    assert repo.list_active_rules(space_id=7) == ()
    with pytest.raises(TodoAutoImportRuleNotFoundError):
        repo.get_owned_rule(rule_id=created.rule_id, created_by="user_2")


def test_checkpoint_is_idempotent_per_source_version_and_cursor_only_moves_forward(
    db,
) -> None:
    repo = TodoAutoImportRepository(db)
    rule = _create(repo)

    first = repo.record_checkpoint(
        rule_id=rule.rule_id,
        checkpoint=_checkpoint(),
    )
    retried = repo.record_checkpoint(
        rule_id=rule.rule_id,
        checkpoint=replace(
            _checkpoint(),
            status=TodoAutoImportProgressStatus.DUPLICATE,
            detail="already imported",
        ),
    )

    assert retried.progress_id == first.progress_id
    assert retried.status is TodoAutoImportProgressStatus.DUPLICATE
    assert retried.detail == "already imported"
    assert len(repo.list_progress(rule_id=rule.rule_id)) == 1
    assert repo.get_rule(rule_id=rule.rule_id).cursor_updated_at == SOURCE_TIME.replace(
        tzinfo=None
    )

    with pytest.raises(TodoAutoImportCursorConflictError):
        repo.record_checkpoint(
            rule_id=rule.rule_id,
            checkpoint=_checkpoint(
                source_item_id="2026112500000000002",
                source_updated_at=SOURCE_TIME + timedelta(minutes=1),
                cursor_updated_at=SOURCE_TIME - timedelta(seconds=1),
            ),
        )


def test_progress_filters_by_container_item_and_source_version(db) -> None:
    repo = TodoAutoImportRepository(db)
    rule = _create(repo)
    repo.record_checkpoint(rule_id=rule.rule_id, checkpoint=_checkpoint())
    second_time = SOURCE_TIME + timedelta(minutes=1)
    second = repo.record_checkpoint(
        rule_id=rule.rule_id,
        checkpoint=_checkpoint(
            source_container_id="P1",
            source_item_id="2026112500000000002",
            source_updated_at=second_time,
            status=TodoAutoImportProgressStatus.SKIPPED,
            todo_id=None,
            cursor_updated_at=None,
        ),
    )

    assert repo.list_progress(
        rule_id=rule.rule_id,
        source_container_id="P1",
    ) == (second,)
    assert repo.list_progress(
        rule_id=rule.rule_id,
        source_item_id=second.source_item_id,
        source_updated_at=second_time,
    ) == (second,)
