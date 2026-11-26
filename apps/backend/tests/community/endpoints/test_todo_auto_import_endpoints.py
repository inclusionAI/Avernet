"""Per-endpoint coverage for external-Agent Dima auto-import persistence."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from injector import singleton

from agentclaw.community.api.task.todo_auto_import_service import (
    TodoAutoImportServiceProtocol,
)
from agentclaw.community.core.task.todos.auto_import_models import (
    TodoAutoImportCheckpoint,
    TodoAutoImportProgressRecord,
    TodoAutoImportProgressStatus,
    TodoAutoImportRuleRecord,
    TodoAutoImportRuleStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoAutoImportRuleDisabledError,
    TodoAutoImportRuleNotFoundError,
    TodoSourceAlreadyImportedError,
)
from agentclaw.community.core.task.todos.models import TodoRecord, TodoStatus
from tests.community.endpoints.todo_endpoint_support import (
    BOT_ID,
    NOW,
    SPACE_ID,
    USER_ID,
    enable_public_auth,
    principal_headers,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_BASE = "/openapi/v1/collaboration/tasks/todos/sources/dima/auto-import-rules"
_QUERY = {"user_id": USER_ID}
_RULE_ID = "todo_auto_import_rule_1"


def _rule(
    *,
    status: TodoAutoImportRuleStatus = TodoAutoImportRuleStatus.ENABLED,
) -> TodoAutoImportRuleRecord:
    return TodoAutoImportRuleRecord(
        rule_id=_RULE_ID,
        space_id=SPACE_ID,
        source_container_ids=("dima-space-1", "dima-project-1"),
        filter_text="只导入需要我处理的高优任务",
        bot_id=BOT_ID,
        status=status,
        cursor_updated_at=None,
        created_by=USER_ID,
        created_at=NOW,
        updated_at=NOW,
    )


def _progress(
    checkpoint: TodoAutoImportCheckpoint | None = None,
) -> TodoAutoImportProgressRecord:
    source = checkpoint or TodoAutoImportCheckpoint(
        source_container_id="dima-project-1",
        source_item_id="dima-task-1",
        source_updated_at=NOW,
        status=TodoAutoImportProgressStatus.IMPORTED,
        todo_id="todo-imported-1",
        detail="created",
        cursor_updated_at=NOW,
    )
    return TodoAutoImportProgressRecord(
        progress_id="todo_auto_import_progress_1",
        rule_id=_RULE_ID,
        source_container_id=source.source_container_id,
        source_item_id=source.source_item_id,
        source_updated_at=source.source_updated_at,
        status=source.status,
        todo_id=source.todo_id,
        detail=source.detail,
        created_at=NOW,
        updated_at=NOW,
    )


def _todo(*, title: str, description: str) -> TodoRecord:
    return TodoRecord(
        todo_id="todo-imported-1",
        space_id=SPACE_ID,
        title=title,
        description=description,
        bot_id=BOT_ID,
        bot_name="Import Bot",
        status=TodoStatus.NOT_STARTED,
        collab_context_id=None,
        created_by=USER_ID,
        created_at=NOW,
        updated_at=NOW,
    )


class _FixedTodoAutoImports:
    def create_rule(self, **kwargs: Any) -> TodoAutoImportRuleRecord:
        return replace(
            _rule(),
            space_id=kwargs["space_id"],
            source_container_ids=kwargs["source_container_ids"],
            filter_text=kwargs["filter_text"],
            bot_id=kwargs["bot_id"],
        )

    def list_rules(self, **kwargs: Any) -> tuple[TodoAutoImportRuleRecord, ...]:
        rule = _rule()
        status = kwargs.get("status")
        if status is not None and TodoAutoImportRuleStatus(status) is not rule.status:
            return ()
        return (rule,)

    def list_active_rules(self, **kwargs: Any) -> tuple[TodoAutoImportRuleRecord, ...]:
        return (_rule(),)

    def get_rule(self, **kwargs: Any) -> TodoAutoImportRuleRecord:
        if kwargs["rule_id"] == "missing":
            raise TodoAutoImportRuleNotFoundError("automatic import rule not found")
        if kwargs["rule_id"] == "disabled":
            return replace(
                _rule(), rule_id="disabled", status=TodoAutoImportRuleStatus.DISABLED
            )
        return _rule()

    def update_rule(self, **kwargs: Any) -> TodoAutoImportRuleRecord:
        rule = self.get_rule(rule_id=kwargs["rule_id"])
        status = kwargs.get("status")
        return replace(
            rule,
            source_container_ids=kwargs.get("source_container_ids")
            or rule.source_container_ids,
            filter_text=kwargs.get("filter_text") or rule.filter_text,
            bot_id=kwargs.get("bot_id") or rule.bot_id,
            status=TodoAutoImportRuleStatus(status) if status else rule.status,
        )

    def record_checkpoint(self, **kwargs: Any) -> TodoAutoImportProgressRecord:
        self.get_rule(rule_id=kwargs["rule_id"])
        return _progress(kwargs["checkpoint"])

    def list_progress(self, **kwargs: Any) -> tuple[TodoAutoImportProgressRecord, ...]:
        self.get_rule(rule_id=kwargs["rule_id"])
        return (_progress(),)

    def create_todo_from_rule(self, **kwargs: Any) -> TodoRecord:
        rule = self.get_rule(rule_id=kwargs["rule_id"])
        if rule.status is TodoAutoImportRuleStatus.DISABLED:
            raise TodoAutoImportRuleDisabledError("rule is disabled")
        if kwargs["source"].item_id == "duplicate":
            raise TodoSourceAlreadyImportedError("todo-existing-1")
        return _todo(title=kwargs["title"], description=kwargs["description"])


def _bind_auto_import_world(world) -> None:
    enable_public_auth(world)
    world.injector.binder.bind(
        TodoAutoImportServiceProtocol,
        to=_FixedTodoAutoImports(),
        scope=singleton,
    )


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="create_rule",
    input=CaseInput(
        query_params=_QUERY,
        headers=principal_headers(),
        json_body={
            "space_id": SPACE_ID,
            "source_container_ids": ["dima-space-1", "dima-project-1"],
            "filter_text": "只导入需要我处理的高优任务",
            "bot_id": BOT_ID,
        },
    ),
    seed=_bind_auto_import_world,
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {
                "rule_id": _RULE_ID,
                "status": "enabled",
                "source_container_ids": ["dima-space-1", "dima-project-1"],
            },
        },
    ),
)
def create_dima_auto_import_rule_ok():
    """A Human can persist one external-Agent scan rule."""


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="empty_source_containers",
    input=CaseInput(
        query_params=_QUERY,
        headers=principal_headers(),
        json_body={
            "space_id": SPACE_ID,
            "source_container_ids": [],
            "filter_text": "只导入高优任务",
            "bot_id": BOT_ID,
        },
    ),
    seed=_bind_auto_import_world,
    expect=ExpectError(status=422),
)
def create_dima_auto_import_rule_rejects_empty_containers():
    """A rule cannot scan an empty Dima container set."""


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="list_rules",
    input=CaseInput(query_params=_QUERY, headers=principal_headers()),
    seed=_bind_auto_import_world,
    expect=ExpectSuccess(
        json_contains={"code": 200000, "data": [{"rule_id": _RULE_ID}]}
    ),
)
def list_dima_auto_import_rules_ok():
    """The authenticated Human can list their saved rules."""


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="wrong_user",
    input=CaseInput(
        query_params={"user_id": "someone-else"},
        headers=principal_headers(),
    ),
    seed=_bind_auto_import_world,
    expect=ExpectError(status=403),
)
def list_dima_auto_import_rules_forbidden():
    """Management remains scoped to the authenticated Human."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/active",
    scenario="app_only_lists_active_rules",
    input=CaseInput(headers=principal_headers(app_only=True)),
    seed=_bind_auto_import_world,
    expect=ExpectSuccess(
        json_contains={
            "code": 200000,
            "data": [{"rule_id": _RULE_ID, "status": "enabled"}],
        }
    ),
)
def list_active_dima_auto_import_rules_app_only_ok():
    """Any authenticated tenant caller can fetch enabled rules."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{rule_id}}",
    scenario="app_only_gets_rule",
    input=CaseInput(
        path_params={"rule_id": _RULE_ID},
        headers=principal_headers(app_only=True),
    ),
    seed=_bind_auto_import_world,
    expect=ExpectSuccess(json_contains={"code": 200000, "data": {"rule_id": _RULE_ID}}),
)
def get_dima_auto_import_rule_app_only_ok():
    """Rule detail has no Human-owner execution gate."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{rule_id}}",
    scenario="missing_rule",
    input=CaseInput(
        path_params={"rule_id": "missing"},
        headers=principal_headers(app_only=True),
    ),
    seed=_bind_auto_import_world,
    expect=ExpectError(status=404),
)
def get_dima_auto_import_rule_not_found():
    """An absent rule is not exposed."""


@endpoint_test(
    method="PATCH",
    path=f"{_BASE}/{{rule_id}}",
    scenario="disable_rule",
    input=CaseInput(
        path_params={"rule_id": _RULE_ID},
        query_params=_QUERY,
        headers=principal_headers(),
        json_body={"status": "disabled"},
    ),
    seed=_bind_auto_import_world,
    expect=ExpectSuccess(
        json_contains={
            "code": 200000,
            "data": {"rule_id": _RULE_ID, "status": "disabled"},
        }
    ),
)
def update_dima_auto_import_rule_ok():
    """The rule owner can disable an external scan rule."""


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{rule_id}}/progress",
    scenario="app_only_lists_progress",
    input=CaseInput(
        path_params={"rule_id": _RULE_ID},
        query_params={"source_container_id": "dima-project-1"},
        headers=principal_headers(app_only=True),
    ),
    seed=_bind_auto_import_world,
    expect=ExpectSuccess(
        json_contains={
            "code": 200000,
            "data": [
                {
                    "progress_id": "todo_auto_import_progress_1",
                    "rule_id": _RULE_ID,
                    "source_item_id": "dima-task-1",
                }
            ],
        }
    ),
)
def list_dima_auto_import_progress_app_only_ok():
    """Any authenticated rule reader can inspect progress."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{rule_id}}/checkpoints",
    scenario="app_only_records_checkpoint",
    input=CaseInput(
        path_params={"rule_id": _RULE_ID},
        headers=principal_headers(app_only=True),
        json_body={
            "source_container_id": "dima-project-1",
            "source_item_id": "dima-task-1",
            "source_updated_at": NOW.isoformat(),
            "status": "imported",
            "todo_id": "todo-imported-1",
            "detail": "created",
            "cursor_updated_at": NOW.isoformat(),
        },
    ),
    seed=_bind_auto_import_world,
    expect=ExpectSuccess(
        json_contains={
            "code": 200000,
            "data": {
                "progress_id": "todo_auto_import_progress_1",
                "status": "imported",
                "todo_id": "todo-imported-1",
            },
        }
    ),
)
def record_dima_auto_import_checkpoint_app_only_ok():
    """Any authenticated rule reader can persist one source-version outcome."""


_CREATE_BODY = {
    "title": "Imported Dima task",
    "description": "Process the imported work item",
    "source": {
        "type": "dima",
        "container_id": "dima-project-1",
        "item_id": "dima-task-1",
        "url": "https://dima.example/items/dima-task-1",
        "updated_at": NOW.isoformat(),
        "metadata": {"processor_id": "user-2", "processor_name": "处理人"},
    },
}


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{rule_id}}/todos",
    scenario="app_only_creates_todo",
    input=CaseInput(
        path_params={"rule_id": _RULE_ID},
        headers=principal_headers(app_only=True),
        json_body=_CREATE_BODY,
    ),
    seed=_bind_auto_import_world,
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {
                "todo_id": "todo-imported-1",
                "space_id": SPACE_ID,
                "bot_id": BOT_ID,
                "title": "Imported Dima task",
            },
        },
    ),
)
def create_todo_from_auto_import_rule_app_only_ok():
    """Any authenticated rule reader can create with the rule-owned scope."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{rule_id}}/todos",
    scenario="cannot_override_rule_scope",
    input=CaseInput(
        path_params={"rule_id": _RULE_ID},
        headers=principal_headers(app_only=True),
        json_body={
            **_CREATE_BODY,
            "space_id": 999,
            "bot_id": "attacker-bot",
            "created_by": "attacker-user",
        },
    ),
    seed=_bind_auto_import_world,
    expect=ExpectError(status=422),
)
def create_todo_from_auto_import_rule_rejects_scope_override():
    """Callers cannot supply ownership, Space, or Bot fields."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{rule_id}}/todos",
    scenario="disabled_rule",
    input=CaseInput(
        path_params={"rule_id": "disabled"},
        headers=principal_headers(app_only=True),
        json_body=_CREATE_BODY,
    ),
    seed=_bind_auto_import_world,
    expect=ExpectError(status=409),
)
def create_todo_from_disabled_auto_import_rule_conflicts():
    """Disabled rules cannot create new Todos."""


@endpoint_test(
    method="POST",
    path=f"{_BASE}/{{rule_id}}/todos",
    scenario="duplicate_source",
    input=CaseInput(
        path_params={"rule_id": _RULE_ID},
        headers=principal_headers(app_only=True),
        json_body={
            **_CREATE_BODY,
            "source": {**_CREATE_BODY["source"], "item_id": "duplicate"},
        },
    ),
    seed=_bind_auto_import_world,
    expect=ExpectError(
        status=409,
        json_contains={"data": {"existing_todo_id": "todo-existing-1"}},
    ),
)
def create_todo_from_auto_import_rule_reports_duplicate():
    """Duplicate source identity returns the existing Todo id."""
