from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.spaces.models import SpaceRecord, SpaceRole, SpaceType
from agentclaw.community.core.task.todos.auto_import_models import (
    TodoAutoImportCheckpoint,
    TodoAutoImportProgressRecord,
    TodoAutoImportProgressStatus,
    TodoAutoImportRuleRecord,
    TodoAutoImportRuleStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoAutoImportRuleDisabledError,
    TodoBotScopeError,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from agentclaw.community.core.task.todos.services.todo_auto_import_service import (
    TodoAutoImportService,
)
from agentclaw.community.core.task.todos.source_models import TodoSourceInput

NOW = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)


def _space(space_id: int = 7) -> SpaceRecord:
    return SpaceRecord(
        id=space_id,
        space_code=f"space-{space_id}",
        space_type=SpaceType.TEAM,
        name="Team Space",
        personal_owner_id=None,
        env="test",
        created_by="user_1",
        updated_by="user_1",
        gmt_created=NOW,
        gmt_modified=NOW,
    )


class _Access:
    def __init__(self) -> None:
        self.roles = {7: SpaceRole.MEMBER, 8: None}

    def require_space_member(self, *, space_id: int, user_id: str):
        if self.roles.get(space_id) is None:
            raise ValidationError("not a member")
        return _space(space_id), SimpleNamespace(user_id=user_id)

    def get_space_role(self, *, space_id: int, user_id: str):
        return self.roles.get(space_id)


class _Bots:
    def list_live_by_bot_id(self, bot_id: str):
        if bot_id == "bot_1":
            return [
                {
                    "bot_id": bot_id,
                    "bot_name": "Bot One",
                    "space_id": 7,
                    "owner_id": "bot_owner",
                }
            ]
        return []


class _Repository:
    def __init__(self) -> None:
        self.rules: dict[str, TodoAutoImportRuleRecord] = {}
        self.progress: list[TodoAutoImportProgressRecord] = []
        self.checkpoint_calls: list[TodoAutoImportCheckpoint] = []

    def create_rule(self, **kwargs: Any) -> TodoAutoImportRuleRecord:
        rule = TodoAutoImportRuleRecord(
            rule_id=f"rule_{len(self.rules) + 1}",
            space_id=kwargs["space_id"],
            source_container_ids=kwargs["source_container_ids"],
            filter_text=kwargs["filter_text"],
            bot_id=kwargs["bot_id"],
            status=TodoAutoImportRuleStatus.ENABLED,
            cursor_updated_at=None,
            created_by=kwargs["created_by"],
            created_at=NOW,
            updated_at=NOW,
        )
        self.rules[rule.rule_id] = rule
        return rule

    def get_rule(self, *, rule_id: str) -> TodoAutoImportRuleRecord:
        return self.rules[rule_id]

    def get_owned_rule(
        self, *, rule_id: str, created_by: str
    ) -> TodoAutoImportRuleRecord:
        rule = self.rules[rule_id]
        assert rule.created_by == created_by
        return rule

    def list_rules(self, **kwargs: Any) -> tuple[TodoAutoImportRuleRecord, ...]:
        return tuple(
            rule
            for rule in self.rules.values()
            if rule.created_by == kwargs["created_by"]
            and (kwargs["space_id"] is None or rule.space_id == kwargs["space_id"])
            and (kwargs["status"] is None or rule.status is kwargs["status"])
        )

    def list_active_rules(self, **kwargs: Any) -> tuple[TodoAutoImportRuleRecord, ...]:
        return tuple(
            rule
            for rule in self.rules.values()
            if rule.status is TodoAutoImportRuleStatus.ENABLED
            and (kwargs["space_id"] is None or rule.space_id == kwargs["space_id"])
        )

    def update_rule(self, **kwargs: Any) -> TodoAutoImportRuleRecord:
        rule = self.get_owned_rule(
            rule_id=kwargs["rule_id"],
            created_by=kwargs["created_by"],
        )
        updated = replace(
            rule,
            source_container_ids=kwargs["source_container_ids"]
            or rule.source_container_ids,
            filter_text=kwargs["filter_text"] or rule.filter_text,
            bot_id=kwargs["bot_id"] or rule.bot_id,
            status=kwargs["status"] or rule.status,
        )
        self.rules[rule.rule_id] = updated
        return updated

    def record_checkpoint(self, **kwargs: Any) -> TodoAutoImportProgressRecord:
        checkpoint = kwargs["checkpoint"]
        self.checkpoint_calls.append(checkpoint)
        progress = TodoAutoImportProgressRecord(
            progress_id="progress_1",
            rule_id=kwargs["rule_id"],
            source_container_id=checkpoint.source_container_id,
            source_item_id=checkpoint.source_item_id,
            source_updated_at=checkpoint.source_updated_at,
            status=checkpoint.status,
            todo_id=checkpoint.todo_id,
            detail=checkpoint.detail,
            created_at=NOW,
            updated_at=NOW,
        )
        self.progress.append(progress)
        return progress

    def list_progress(self, **kwargs: Any) -> tuple[TodoAutoImportProgressRecord, ...]:
        return tuple(self.progress[: kwargs["limit"]])


class _Todos:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_todo(self, **kwargs: Any) -> TodoRecord:
        self.calls.append(kwargs)
        return TodoRecord(
            todo_id="todo_1",
            space_id=kwargs["space_id"],
            title=kwargs["title"],
            description=kwargs["description"],
            bot_id=kwargs["bot_id"],
            bot_name="Bot One",
            status=TodoStatus.NOT_STARTED,
            collab_context_id=None,
            created_by=kwargs["actor_id"],
            created_at=NOW,
            updated_at=NOW,
        )


def _service() -> tuple[TodoAutoImportService, _Repository, _Access, _Todos]:
    repository = _Repository()
    access = _Access()
    todos = _Todos()
    return (
        TodoAutoImportService(repository, access, _Bots(), todos),
        repository,
        access,
        todos,
    )


def _checkpoint(**overrides: Any) -> TodoAutoImportCheckpoint:
    values = {
        "source_container_id": "W1",
        "source_item_id": "item_1",
        "source_updated_at": NOW,
        "status": TodoAutoImportProgressStatus.IMPORTED,
        "todo_id": "todo_1",
        "detail": "created",
        "cursor_updated_at": NOW,
    }
    values.update(overrides)
    return TodoAutoImportCheckpoint(**values)


def _source(**overrides: Any) -> TodoSourceInput:
    values = {
        "source_type": "dima",
        "container_id": "W1",
        "item_id": "item_1",
        "url": None,
        "updated_at": NOW.isoformat(),
        "metadata": {"processor_id": "user_2"},
    }
    values.update(overrides)
    return TodoSourceInput(**values)


def test_create_update_and_active_list_validate_space_and_bot_scope() -> None:
    service, repository, _, _ = _service()

    rule = service.create_rule(
        space_id=7,
        actor_id="user_1",
        source_container_ids=("W1", "W1", "P1"),
        filter_text=" 只导入高优任务 ",
        bot_id="bot_1",
    )
    assert rule.source_container_ids == ("W1", "P1")
    assert rule.filter_text == "只导入高优任务"
    assert service.list_active_rules() == (rule,)

    updated = service.update_rule(
        rule_id=rule.rule_id,
        actor_id="user_1",
        status="disabled",
    )
    assert updated.status is TodoAutoImportRuleStatus.DISABLED
    assert service.list_active_rules() == ()

    with pytest.raises(TodoBotScopeError):
        service.update_rule(
            rule_id=rule.rule_id,
            actor_id="user_1",
            bot_id="other_bot",
        )
    assert repository.rules[rule.rule_id].bot_id == "bot_1"


def test_cross_space_owner_list_hides_rules_after_membership_is_lost() -> None:
    service, repository, access, _ = _service()
    visible = service.create_rule(
        space_id=7,
        actor_id="user_1",
        source_container_ids=("W1",),
        filter_text="all",
        bot_id="bot_1",
    )
    repository.rules["rule_hidden"] = replace(
        visible, rule_id="rule_hidden", space_id=8
    )

    assert service.list_rules(actor_id="user_1") == (visible,)
    access.roles[7] = None
    assert service.list_rules(actor_id="user_1") == ()


def test_checkpoint_and_progress_do_not_require_rule_owner() -> None:
    service, repository, _, _ = _service()
    rule = service.create_rule(
        space_id=7,
        actor_id="user_1",
        source_container_ids=("W1",),
        filter_text="all",
        bot_id="bot_1",
    )

    result = service.record_checkpoint(
        rule_id=rule.rule_id,
        checkpoint=_checkpoint(),
    )
    assert result.status is TodoAutoImportProgressStatus.IMPORTED
    assert service.list_progress(rule_id=rule.rule_id) == (result,)
    assert repository.checkpoint_calls == [_checkpoint()]


def test_checkpoint_validates_outcome_container_and_aware_times() -> None:
    service, _, _, _ = _service()
    rule = service.create_rule(
        space_id=7,
        actor_id="user_1",
        source_container_ids=("W1",),
        filter_text="all",
        bot_id="bot_1",
    )

    invalid = [
        _checkpoint(source_container_id="P9"),
        _checkpoint(status=TodoAutoImportProgressStatus.IMPORTED, todo_id=None),
        _checkpoint(source_updated_at=NOW.replace(tzinfo=None)),
        _checkpoint(cursor_updated_at=NOW.replace(tzinfo=None)),
        _checkpoint(status="unknown"),
    ]
    for checkpoint in invalid:
        with pytest.raises(ValidationError):
            service.record_checkpoint(rule_id=rule.rule_id, checkpoint=checkpoint)


def test_failed_checkpoint_may_omit_cursor_and_todo() -> None:
    service, repository, _, _ = _service()
    rule = service.create_rule(
        space_id=7,
        actor_id="user_1",
        source_container_ids=("W1",),
        filter_text="all",
        bot_id="bot_1",
    )

    service.record_checkpoint(
        rule_id=rule.rule_id,
        checkpoint=_checkpoint(
            status=TodoAutoImportProgressStatus.FAILED,
            todo_id=None,
            cursor_updated_at=None,
        ),
    )

    assert repository.checkpoint_calls[-1].cursor_updated_at is None
    assert repository.checkpoint_calls[-1].todo_id is None


def test_progress_filter_rejects_naive_source_time() -> None:
    service, _, _, _ = _service()
    rule = service.create_rule(
        space_id=7,
        actor_id="user_1",
        source_container_ids=("W1",),
        filter_text="all",
        bot_id="bot_1",
    )

    with pytest.raises(ValidationError, match="timezone"):
        service.list_progress(
            rule_id=rule.rule_id,
            source_updated_at=NOW.replace(tzinfo=None),
        )


def test_any_rule_reader_can_create_with_rule_owned_scope() -> None:
    service, _, _, todos = _service()
    rule = service.create_rule(
        space_id=7,
        actor_id="user_1",
        source_container_ids=("W1",),
        filter_text="all",
        bot_id="bot_1",
    )

    result = service.create_todo_from_rule(
        rule_id=rule.rule_id,
        title="Imported task",
        description="Created by an external Agent",
        source=_source(),
    )

    assert result.todo_id == "todo_1"
    assert todos.calls == [
        {
            "space_id": 7,
            "actor_id": "user_1",
            "title": "Imported task",
            "description": "Created by an external Agent",
            "bot_id": "bot_1",
            "source": _source(),
        }
    ]


def test_create_from_rule_rejects_disabled_or_unconfigured_source() -> None:
    service, repository, _, _ = _service()
    rule = service.create_rule(
        space_id=7,
        actor_id="user_1",
        source_container_ids=("W1",),
        filter_text="all",
        bot_id="bot_1",
    )

    with pytest.raises(ValidationError, match="not configured"):
        service.create_todo_from_rule(
            rule_id=rule.rule_id,
            title="Imported task",
            description="",
            source=_source(container_id="P9"),
        )

    repository.rules[rule.rule_id] = replace(
        rule, status=TodoAutoImportRuleStatus.DISABLED
    )
    with pytest.raises(TodoAutoImportRuleDisabledError):
        service.create_todo_from_rule(
            rule_id=rule.rule_id,
            title="Imported task",
            description="",
            source=_source(),
        )
