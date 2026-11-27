"""Service API for Dima auto-import rule management and agent checkpoints."""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.auto_import_models import (
    DEFAULT_TODO_AUTO_IMPORT_CRON_EXPRESSION,
    DEFAULT_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
    TodoAutoImportCheckpoint,
    TodoAutoImportProgressRecord,
    TodoAutoImportRuleRecord,
    TodoAutoImportRuleStatus,
)
from agentclaw.community.core.task.todos.models import TodoRecord
from agentclaw.community.core.task.todos.source_models import TodoSourceInput


@runtime_checkable
class TodoAutoImportServiceProtocol(Protocol):
    @abstractmethod
    def create_rule(
        self,
        *,
        space_id: int,
        actor_id: str,
        source_container_ids: tuple[str, ...],
        filter_text: str,
        bot_id: str,
        scheduler_bot_id: str,
        trigger_interval_seconds: int = DEFAULT_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
        cron_expression: str = DEFAULT_TODO_AUTO_IMPORT_CRON_EXPRESSION,
    ) -> TodoAutoImportRuleRecord: ...

    @abstractmethod
    def list_rules(
        self,
        *,
        actor_id: str,
        space_id: int | None = None,
        status: TodoAutoImportRuleStatus | str | None = None,
    ) -> tuple[TodoAutoImportRuleRecord, ...]: ...

    @abstractmethod
    def list_active_rules(
        self,
        *,
        space_id: int | None = None,
    ) -> tuple[TodoAutoImportRuleRecord, ...]: ...

    @abstractmethod
    def get_rule(self, *, rule_id: str) -> TodoAutoImportRuleRecord: ...

    @abstractmethod
    def update_rule(
        self,
        *,
        rule_id: str,
        actor_id: str,
        source_container_ids: tuple[str, ...] | None = None,
        filter_text: str | None = None,
        bot_id: str | None = None,
        scheduler_bot_id: str | None = None,
        trigger_interval_seconds: int | None = None,
        cron_expression: str | None = None,
        status: TodoAutoImportRuleStatus | str | None = None,
    ) -> TodoAutoImportRuleRecord: ...

    @abstractmethod
    def record_checkpoint(
        self,
        *,
        rule_id: str,
        checkpoint: TodoAutoImportCheckpoint,
    ) -> TodoAutoImportProgressRecord: ...

    @abstractmethod
    def list_progress(
        self,
        *,
        rule_id: str,
        source_container_id: str | None = None,
        source_item_id: str | None = None,
        source_updated_at: datetime | None = None,
        limit: int = 100,
    ) -> tuple[TodoAutoImportProgressRecord, ...]: ...

    @abstractmethod
    def create_todo_from_rule(
        self,
        *,
        rule_id: str,
        title: str,
        description: str,
        source: TodoSourceInput,
    ) -> TodoRecord: ...
