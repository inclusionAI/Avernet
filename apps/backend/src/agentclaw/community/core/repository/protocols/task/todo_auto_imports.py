"""Persistence contract for automatic Dima import rules and checkpoints."""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.auto_import_models import (
        TodoAutoImportCheckpoint,
        TodoAutoImportProgressRecord,
        TodoAutoImportRuleRecord,
        TodoAutoImportRuleStatus,
    )


@runtime_checkable
class TodoAutoImportRepositoryProtocol(Protocol):
    @abstractmethod
    def create_rule(
        self,
        *,
        space_id: int,
        source_container_ids: tuple[str, ...],
        filter_text: str,
        bot_id: str,
        created_by: str,
    ) -> TodoAutoImportRuleRecord: ...

    @abstractmethod
    def get_rule(self, *, rule_id: str) -> TodoAutoImportRuleRecord: ...

    @abstractmethod
    def get_owned_rule(
        self, *, rule_id: str, created_by: str
    ) -> TodoAutoImportRuleRecord: ...

    @abstractmethod
    def list_rules(
        self,
        *,
        created_by: str,
        space_id: int | None = None,
        status: TodoAutoImportRuleStatus | None = None,
    ) -> tuple[TodoAutoImportRuleRecord, ...]: ...

    @abstractmethod
    def list_active_rules(
        self,
        *,
        space_id: int | None = None,
    ) -> tuple[TodoAutoImportRuleRecord, ...]: ...

    @abstractmethod
    def update_rule(
        self,
        *,
        rule_id: str,
        created_by: str,
        source_container_ids: tuple[str, ...] | None = None,
        filter_text: str | None = None,
        bot_id: str | None = None,
        status: TodoAutoImportRuleStatus | None = None,
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
