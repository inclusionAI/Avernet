"""SQLAlchemy persistence for external Dima auto-import rules and checkpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from injector import inject
from sqlalchemy import desc

from agentclaw.community.core.repository.implementations.task.todos.models import (
    TodoAutoImportProgressModel,
    TodoAutoImportRuleModel,
)
from agentclaw.community.core.repository.protocols.task.todo_auto_imports import (
    TodoAutoImportRepositoryProtocol,
)
from agentclaw.community.core.task.todos.auto_import_models import (
    TodoAutoImportCheckpoint,
    TodoAutoImportProgressRecord,
    TodoAutoImportRuleRecord,
    TodoAutoImportRuleStatus,
)
from agentclaw.community.core.task.todos.errors import (
    TodoAutoImportCursorConflictError,
    TodoAutoImportRuleNotFoundError,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class TodoAutoImportRepository(TodoAutoImportRepositoryProtocol):
    """Persist rule configuration and per-source-version Agent outcomes."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    @staticmethod
    def _scope(query, model):
        return query.filter(
            model.env == get_current_env(),
            model.avernet_tenant == get_current_avernet_tenant(),
        )

    @classmethod
    def _rule_query(cls, db, *, rule_id: str):
        return cls._scope(
            db.query(TodoAutoImportRuleModel), TodoAutoImportRuleModel
        ).filter(TodoAutoImportRuleModel.rule_id == rule_id)

    @classmethod
    def _owned_rule_query(cls, db, *, rule_id: str, created_by: str):
        return cls._rule_query(db, rule_id=rule_id).filter(
            TodoAutoImportRuleModel.created_by == created_by
        )

    @classmethod
    def _require_rule(cls, db, *, rule_id: str, lock: bool = False):
        query = cls._rule_query(db, rule_id=rule_id)
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise TodoAutoImportRuleNotFoundError(
                f"Todo auto-import rule {rule_id} not found"
            )
        return row

    @classmethod
    def _require_owned_rule(
        cls,
        db,
        *,
        rule_id: str,
        created_by: str,
        lock: bool = False,
    ):
        query = cls._owned_rule_query(
            db,
            rule_id=rule_id,
            created_by=created_by,
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise TodoAutoImportRuleNotFoundError(
                f"Todo auto-import rule {rule_id} not found"
            )
        return row

    def create_rule(
        self,
        *,
        space_id: int,
        source_container_ids: tuple[str, ...],
        filter_text: str,
        bot_id: str,
        scheduler_bot_id: str,
        trigger_interval_seconds: int,
        cron_expression: str,
        created_by: str,
    ) -> TodoAutoImportRuleRecord:
        with self._transaction() as db:
            row = TodoAutoImportRuleModel(
                rule_id=f"todo_air_{uuid.uuid4().hex}",
                space_id=space_id,
                source_type="dima",
                source_container_ids_json=json.dumps(
                    source_container_ids,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                filter_text=filter_text,
                bot_id=bot_id,
                scheduler_bot_id=scheduler_bot_id,
                trigger_interval_seconds=trigger_interval_seconds,
                cron_expression=cron_expression,
                status=TodoAutoImportRuleStatus.ENABLED.value,
                cursor_updated_at=None,
                created_by=created_by,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            return row.to_record()

    def get_rule(self, *, rule_id: str) -> TodoAutoImportRuleRecord:
        with self._db.orm_session() as db:
            return self._require_rule(db, rule_id=rule_id).to_record()

    def get_owned_rule(
        self, *, rule_id: str, created_by: str
    ) -> TodoAutoImportRuleRecord:
        with self._db.orm_session() as db:
            return self._require_owned_rule(
                db,
                rule_id=rule_id,
                created_by=created_by,
            ).to_record()

    def list_rules(
        self,
        *,
        created_by: str,
        space_id: int | None = None,
        status: TodoAutoImportRuleStatus | None = None,
    ) -> tuple[TodoAutoImportRuleRecord, ...]:
        with self._db.orm_session() as db:
            query = self._scope(
                db.query(TodoAutoImportRuleModel), TodoAutoImportRuleModel
            ).filter(TodoAutoImportRuleModel.created_by == created_by)
            if space_id is not None:
                query = query.filter(TodoAutoImportRuleModel.space_id == space_id)
            if status is not None:
                query = query.filter(TodoAutoImportRuleModel.status == status.value)
            rows = query.order_by(
                desc(TodoAutoImportRuleModel.gmt_modified),
                desc(TodoAutoImportRuleModel.id),
            ).all()
            return tuple(row.to_record() for row in rows)

    def list_active_rules(
        self,
        *,
        space_id: int | None = None,
    ) -> tuple[TodoAutoImportRuleRecord, ...]:
        with self._db.orm_session() as db:
            query = self._scope(
                db.query(TodoAutoImportRuleModel), TodoAutoImportRuleModel
            ).filter(
                TodoAutoImportRuleModel.status == TodoAutoImportRuleStatus.ENABLED.value
            )
            if space_id is not None:
                query = query.filter(TodoAutoImportRuleModel.space_id == space_id)
            rows = query.order_by(
                desc(TodoAutoImportRuleModel.gmt_modified),
                desc(TodoAutoImportRuleModel.id),
            ).all()
            return tuple(row.to_record() for row in rows)

    def update_rule(
        self,
        *,
        rule_id: str,
        created_by: str,
        source_container_ids: tuple[str, ...] | None = None,
        filter_text: str | None = None,
        bot_id: str | None = None,
        scheduler_bot_id: str | None = None,
        trigger_interval_seconds: int | None = None,
        cron_expression: str | None = None,
        status: TodoAutoImportRuleStatus | None = None,
    ) -> TodoAutoImportRuleRecord:
        with self._transaction() as db:
            row = self._require_owned_rule(
                db, rule_id=rule_id, created_by=created_by, lock=True
            )
            if source_container_ids is not None:
                row.source_container_ids_json = json.dumps(
                    source_container_ids,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if filter_text is not None:
                row.filter_text = filter_text
            if bot_id is not None:
                row.bot_id = bot_id
            if scheduler_bot_id is not None:
                row.scheduler_bot_id = scheduler_bot_id
            if trigger_interval_seconds is not None:
                row.trigger_interval_seconds = trigger_interval_seconds
            if cron_expression is not None:
                row.cron_expression = cron_expression
            if status is not None:
                row.status = status.value
            db.flush()
            db.refresh(row)
            return row.to_record()

    def record_checkpoint(
        self,
        *,
        rule_id: str,
        checkpoint: TodoAutoImportCheckpoint,
    ) -> TodoAutoImportProgressRecord:
        source_updated_at = _storage_datetime(checkpoint.source_updated_at)
        cursor_updated_at = (
            _storage_datetime(checkpoint.cursor_updated_at)
            if checkpoint.cursor_updated_at is not None
            else None
        )
        with self._transaction() as db:
            rule = self._require_rule(db, rule_id=rule_id, lock=True)
            current_cursor = (
                _storage_datetime(rule.cursor_updated_at)
                if rule.cursor_updated_at is not None
                else None
            )
            if (
                cursor_updated_at is not None
                and current_cursor is not None
                and cursor_updated_at < current_cursor
            ):
                raise TodoAutoImportCursorConflictError(
                    "auto-import cursor cannot move backwards"
                )

            progress = (
                self._scope(
                    db.query(TodoAutoImportProgressModel), TodoAutoImportProgressModel
                )
                .filter(
                    TodoAutoImportProgressModel.rule_id == rule_id,
                    TodoAutoImportProgressModel.source_container_id
                    == checkpoint.source_container_id,
                    TodoAutoImportProgressModel.source_item_id
                    == checkpoint.source_item_id,
                    TodoAutoImportProgressModel.source_updated_at == source_updated_at,
                )
                .with_for_update()
                .one_or_none()
            )
            if progress is None:
                progress = TodoAutoImportProgressModel(
                    progress_id=f"todo_aip_{uuid.uuid4().hex}",
                    rule_id=rule_id,
                    source_container_id=checkpoint.source_container_id,
                    source_item_id=checkpoint.source_item_id,
                    source_updated_at=source_updated_at,
                    status=checkpoint.status.value,
                    todo_id=checkpoint.todo_id,
                    detail=checkpoint.detail,
                    env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                )
                db.add(progress)
            else:
                progress.status = checkpoint.status.value
                progress.todo_id = checkpoint.todo_id
                progress.detail = checkpoint.detail

            if cursor_updated_at is not None and (
                current_cursor is None or cursor_updated_at > current_cursor
            ):
                rule.cursor_updated_at = cursor_updated_at
            db.flush()
            db.refresh(progress)
            return progress.to_record()

    def list_progress(
        self,
        *,
        rule_id: str,
        source_container_id: str | None = None,
        source_item_id: str | None = None,
        source_updated_at: datetime | None = None,
        limit: int = 100,
    ) -> tuple[TodoAutoImportProgressRecord, ...]:
        with self._db.orm_session() as db:
            self._require_rule(db, rule_id=rule_id)
            query = self._scope(
                db.query(TodoAutoImportProgressModel), TodoAutoImportProgressModel
            ).filter(TodoAutoImportProgressModel.rule_id == rule_id)
            if source_container_id is not None:
                query = query.filter(
                    TodoAutoImportProgressModel.source_container_id
                    == source_container_id
                )
            if source_item_id is not None:
                query = query.filter(
                    TodoAutoImportProgressModel.source_item_id == source_item_id
                )
            if source_updated_at is not None:
                query = query.filter(
                    TodoAutoImportProgressModel.source_updated_at
                    == _storage_datetime(source_updated_at)
                )
            rows = (
                query.order_by(
                    desc(TodoAutoImportProgressModel.source_updated_at),
                    desc(TodoAutoImportProgressModel.id),
                )
                .limit(limit)
                .all()
            )
            return tuple(row.to_record() for row in rows)


def _storage_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
