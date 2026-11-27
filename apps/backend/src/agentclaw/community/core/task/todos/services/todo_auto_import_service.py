"""Policy layer for Dima auto-import rules consumed by an external Agent."""

from __future__ import annotations

from datetime import datetime

from injector import inject

from agentclaw.community.core.errors import ValidationError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.task.todo_auto_imports import (
    TodoAutoImportRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.todos.auto_import_models import (
    DEFAULT_TODO_AUTO_IMPORT_CRON_EXPRESSION,
    DEFAULT_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
    MAX_TODO_AUTO_IMPORT_CRON_LENGTH,
    MAX_TODO_AUTO_IMPORT_FILTER_LENGTH,
    MAX_TODO_AUTO_IMPORT_PROGRESS_DETAIL_LENGTH,
    MAX_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
    MIN_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS,
    TodoAutoImportCheckpoint,
    TodoAutoImportProgressRecord,
    TodoAutoImportProgressStatus,
    TodoAutoImportRuleRecord,
    TodoAutoImportRuleStatus,
    normalize_source_container_ids,
)
from agentclaw.community.core.task.todos.auto_import_service_protocol import (
    TodoAutoImportServiceProtocol,
)
from agentclaw.community.core.task.todos.bot_scope import resolve_todo_bot
from agentclaw.community.core.task.todos.errors import (
    TodoAutoImportRuleDisabledError,
    TodoBotAmbiguousError,
    TodoBotScopeError,
)
from agentclaw.community.core.task.todos.models import (
    MAX_ACTOR_ID_LENGTH,
    MAX_BOT_ID_LENGTH,
    MAX_TODO_ID_LENGTH,
    TodoRecord,
)
from agentclaw.community.core.task.todos.service_protocol import TodoServiceProtocol
from agentclaw.community.core.task.todos.source_models import (
    MAX_TODO_SOURCE_CONTAINER_ID_LENGTH,
    MAX_TODO_SOURCE_ITEM_ID_LENGTH,
    TodoSourceInput,
)


class TodoAutoImportService(TodoAutoImportServiceProtocol):
    """Manage configuration only; scanning and Dima calls stay outside Backend."""

    @inject
    def __init__(
        self,
        repository: TodoAutoImportRepositoryProtocol,
        space_access: SpaceAccessServiceProtocol,
        bot_repository: BotRepository,
        todo_service: TodoServiceProtocol,
    ) -> None:
        self._repository = repository
        self._space_access = space_access
        self._bot_repository = bot_repository
        self._todo_service = todo_service

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
    ) -> TodoAutoImportRuleRecord:
        actor = _required_text(actor_id, "actor_id", MAX_ACTOR_ID_LENGTH)
        normalized_space_id = _space_id(space_id)
        containers = _containers(source_container_ids)
        rule_filter = _required_text(
            filter_text, "filter_text", MAX_TODO_AUTO_IMPORT_FILTER_LENGTH
        )
        selected_bot_id = _required_text(bot_id, "bot_id", MAX_BOT_ID_LENGTH)
        selected_scheduler_bot_id = _required_text(
            scheduler_bot_id, "scheduler_bot_id", MAX_BOT_ID_LENGTH
        )
        normalized_trigger_interval = _trigger_interval_seconds(
            trigger_interval_seconds
        )
        normalized_cron = _cron_expression(cron_expression)
        space, _member = self._space_access.require_space_member(
            space_id=normalized_space_id,
            user_id=actor,
        )
        resolve_todo_bot(
            bot_repository=self._bot_repository,
            space=space,
            actor_id=actor,
            bot_id=selected_bot_id,
        )
        _require_creator_owned_bot(
            bot_repository=self._bot_repository,
            actor_id=actor,
            bot_id=selected_scheduler_bot_id,
        )
        return self._repository.create_rule(
            space_id=normalized_space_id,
            source_container_ids=containers,
            filter_text=rule_filter,
            bot_id=selected_bot_id,
            scheduler_bot_id=selected_scheduler_bot_id,
            trigger_interval_seconds=normalized_trigger_interval,
            cron_expression=normalized_cron,
            created_by=actor,
        )

    def list_rules(
        self,
        *,
        actor_id: str,
        space_id: int | None = None,
        status: TodoAutoImportRuleStatus | str | None = None,
    ) -> tuple[TodoAutoImportRuleRecord, ...]:
        actor = _required_text(actor_id, "actor_id", MAX_ACTOR_ID_LENGTH)
        normalized_space_id = _space_id(space_id) if space_id is not None else None
        normalized_status = _rule_status(status)
        if normalized_space_id is not None:
            self._space_access.require_space_member(
                space_id=normalized_space_id,
                user_id=actor,
            )
        rules = self._repository.list_rules(
            created_by=actor,
            space_id=normalized_space_id,
            status=normalized_status,
        )
        if normalized_space_id is not None:
            return rules
        return tuple(
            rule
            for rule in rules
            if self._space_access.get_space_role(space_id=rule.space_id, user_id=actor)
            is not None
        )

    def list_active_rules(
        self,
        *,
        space_id: int | None = None,
    ) -> tuple[TodoAutoImportRuleRecord, ...]:
        normalized_space_id = _space_id(space_id) if space_id is not None else None
        return self._repository.list_active_rules(space_id=normalized_space_id)

    def get_rule(self, *, rule_id: str) -> TodoAutoImportRuleRecord:
        return self._repository.get_rule(
            rule_id=_required_text(rule_id, "rule_id", 64),
        )

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
    ) -> TodoAutoImportRuleRecord:
        actor = _required_text(actor_id, "actor_id", MAX_ACTOR_ID_LENGTH)
        normalized_rule_id = _required_text(rule_id, "rule_id", 64)
        rule = self._repository.get_owned_rule(
            rule_id=normalized_rule_id,
            created_by=actor,
        )
        space, _member = self._space_access.require_space_member(
            space_id=rule.space_id,
            user_id=actor,
        )
        containers = (
            _containers(source_container_ids)
            if source_container_ids is not None
            else None
        )
        rule_filter = (
            _required_text(
                filter_text, "filter_text", MAX_TODO_AUTO_IMPORT_FILTER_LENGTH
            )
            if filter_text is not None
            else None
        )
        selected_bot_id = (
            _required_text(bot_id, "bot_id", MAX_BOT_ID_LENGTH)
            if bot_id is not None
            else None
        )
        selected_scheduler_bot_id = (
            _required_text(
                scheduler_bot_id, "scheduler_bot_id", MAX_BOT_ID_LENGTH
            )
            if scheduler_bot_id is not None
            else None
        )
        normalized_trigger_interval = (
            _trigger_interval_seconds(trigger_interval_seconds)
            if trigger_interval_seconds is not None
            else None
        )
        normalized_cron = (
            _cron_expression(cron_expression) if cron_expression is not None else None
        )
        normalized_status = _rule_status(status)
        if all(
            value is None
            for value in (
                containers,
                rule_filter,
                selected_bot_id,
                selected_scheduler_bot_id,
                normalized_trigger_interval,
                normalized_cron,
                normalized_status,
            )
        ):
            raise ValidationError("at least one auto-import rule field is required")
        if selected_bot_id is not None:
            resolve_todo_bot(
                bot_repository=self._bot_repository,
                space=space,
                actor_id=actor,
                bot_id=selected_bot_id,
            )
        if selected_scheduler_bot_id is not None:
            _require_creator_owned_bot(
                bot_repository=self._bot_repository,
                actor_id=actor,
                bot_id=selected_scheduler_bot_id,
            )
        return self._repository.update_rule(
            rule_id=normalized_rule_id,
            created_by=actor,
            source_container_ids=containers,
            filter_text=rule_filter,
            bot_id=selected_bot_id,
            scheduler_bot_id=selected_scheduler_bot_id,
            trigger_interval_seconds=normalized_trigger_interval,
            cron_expression=normalized_cron,
            status=normalized_status,
        )

    def record_checkpoint(
        self,
        *,
        rule_id: str,
        checkpoint: TodoAutoImportCheckpoint,
    ) -> TodoAutoImportProgressRecord:
        normalized_rule_id = _required_text(rule_id, "rule_id", 64)
        rule = self._repository.get_rule(rule_id=normalized_rule_id)
        container_id = _required_text(
            checkpoint.source_container_id,
            "source_container_id",
            MAX_TODO_SOURCE_CONTAINER_ID_LENGTH,
        )
        if container_id not in rule.source_container_ids:
            raise ValidationError("source container is not configured on this rule")
        item_id = _required_text(
            checkpoint.source_item_id,
            "source_item_id",
            MAX_TODO_SOURCE_ITEM_ID_LENGTH,
        )
        if not isinstance(checkpoint.source_updated_at, datetime):
            raise ValidationError("source_updated_at must be a datetime")
        source_updated_at = _aware_datetime(
            checkpoint.source_updated_at,
            "source_updated_at",
        )
        try:
            progress_status = TodoAutoImportProgressStatus(checkpoint.status)
        except ValueError as exc:
            raise ValidationError("unsupported auto-import progress status") from exc
        cursor_updated_at = (
            _aware_datetime(checkpoint.cursor_updated_at, "cursor_updated_at")
            if checkpoint.cursor_updated_at is not None
            else None
        )
        todo_id = _optional_text(checkpoint.todo_id, "todo_id", MAX_TODO_ID_LENGTH)
        if (
            progress_status
            in (
                TodoAutoImportProgressStatus.IMPORTED,
                TodoAutoImportProgressStatus.DUPLICATE,
            )
            and todo_id is None
        ):
            raise ValidationError(
                "todo_id is required for imported or duplicate outcomes"
            )
        detail = _text_allow_empty(
            checkpoint.detail,
            "detail",
            MAX_TODO_AUTO_IMPORT_PROGRESS_DETAIL_LENGTH,
        )
        return self._repository.record_checkpoint(
            rule_id=normalized_rule_id,
            checkpoint=TodoAutoImportCheckpoint(
                source_container_id=container_id,
                source_item_id=item_id,
                source_updated_at=source_updated_at,
                status=progress_status,
                todo_id=todo_id,
                detail=detail,
                cursor_updated_at=cursor_updated_at,
            ),
        )

    def list_progress(
        self,
        *,
        rule_id: str,
        source_container_id: str | None = None,
        source_item_id: str | None = None,
        source_updated_at: datetime | None = None,
        limit: int = 100,
    ) -> tuple[TodoAutoImportProgressRecord, ...]:
        rule = self.get_rule(rule_id=rule_id)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 500
        ):
            raise ValidationError("limit must be between 1 and 500")
        container_id = _optional_text(
            source_container_id,
            "source_container_id",
            MAX_TODO_SOURCE_CONTAINER_ID_LENGTH,
        )
        item_id = _optional_text(
            source_item_id,
            "source_item_id",
            MAX_TODO_SOURCE_ITEM_ID_LENGTH,
        )
        normalized_source_updated_at = (
            _aware_datetime(source_updated_at, "source_updated_at")
            if source_updated_at is not None
            else None
        )
        return self._repository.list_progress(
            rule_id=rule.rule_id,
            source_container_id=container_id,
            source_item_id=item_id,
            source_updated_at=normalized_source_updated_at,
            limit=limit,
        )

    def create_todo_from_rule(
        self,
        *,
        rule_id: str,
        title: str,
        description: str,
        source: TodoSourceInput,
    ) -> TodoRecord:
        rule = self.get_rule(rule_id=rule_id)
        if rule.status is not TodoAutoImportRuleStatus.ENABLED:
            raise TodoAutoImportRuleDisabledError("Todo auto-import rule is disabled")
        if not isinstance(source, TodoSourceInput) or source.source_type != "dima":
            raise ValidationError("source must be a Dima source")
        container_id = _required_text(
            source.container_id,
            "source.container_id",
            MAX_TODO_SOURCE_CONTAINER_ID_LENGTH,
        )
        if container_id not in rule.source_container_ids:
            raise ValidationError("source container is not configured on this rule")
        return self._todo_service.create_todo(
            space_id=rule.space_id,
            actor_id=rule.created_by,
            title=title,
            description=description,
            bot_id=rule.bot_id,
            source=source,
        )


def _space_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValidationError("space_id must be a positive integer")
    return value


def _containers(values: object) -> tuple[str, ...]:
    try:
        containers = normalize_source_container_ids(values)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    for container_id in containers:
        if len(container_id) > MAX_TODO_SOURCE_CONTAINER_ID_LENGTH:
            raise ValidationError("source container id exceeds the maximum length")
    return containers


def _require_creator_owned_bot(
    *, bot_repository: BotRepository, actor_id: str, bot_id: str
) -> None:
    matches = bot_repository.list_matches_by_current_owner(bot_id, actor_id)
    if not matches:
        raise TodoBotScopeError("Scheduler Bot must be owned by the rule creator")
    if len(matches) > 1:
        raise TodoBotAmbiguousError("Scheduler Bot id is ambiguous for the rule creator")


def _trigger_interval_seconds(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError("trigger_interval_seconds must be an integer")
    if not (
        MIN_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS
        <= value
        <= MAX_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS
    ):
        raise ValidationError(
            f"trigger_interval_seconds must be between {MIN_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS} "
            f"and {MAX_TODO_AUTO_IMPORT_TRIGGER_INTERVAL_SECONDS}"
        )
    return value


def _cron_expression(value: object) -> str:
    normalized = _required_text(
        value, "cron_expression", MAX_TODO_AUTO_IMPORT_CRON_LENGTH
    )
    if len(normalized.split()) != 5:
        raise ValidationError("cron_expression must contain exactly 5 fields")
    return normalized


def _rule_status(
    value: TodoAutoImportRuleStatus | str | None,
) -> TodoAutoImportRuleStatus | None:
    if value is None:
        return None
    try:
        return TodoAutoImportRuleStatus(value)
    except ValueError as exc:
        raise ValidationError("unsupported auto-import rule status") from exc


def _required_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} is required")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValidationError(f"{field} exceeds the maximum length")
    return normalized


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field, maximum)


def _text_allow_empty(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValidationError(f"{field} exceeds the maximum length")
    return normalized


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field} must include a timezone")
    return value
