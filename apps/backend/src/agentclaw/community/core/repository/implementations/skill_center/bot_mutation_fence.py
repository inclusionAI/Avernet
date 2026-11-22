"""Serialize one persisted Bot's capability mutations in their DB transaction."""

from contextlib import contextmanager

from sqlalchemy.orm import Query, Session

from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneLockUnavailableError,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def _capability_bots(
    session: Session, *, bot_id: str, entity_id: str, env: str | None,
) -> Query[BotModel]:
    return session.query(BotModel).filter(
        BotModel.avernet_tenant == get_current_avernet_tenant(),
        BotModel.env == (env or get_current_env()),
        BotModel.bot_id == bot_id,
        BotModel.entity_id == entity_id,
        BotModel.is_delete == 0,
    )


def _begin_sqlite_write(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        # SQLite has no FOR UPDATE. Acquire the write lock before making a
        # projection or capability decision from the current committed state.
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def find_capability_bot(
    session: Session, *, bot_id: str, entity_id: str, lock: bool = False,
) -> BotModel | None:
    query = _capability_bots(session, bot_id=bot_id, entity_id=entity_id, env=None)
    if lock:
        _begin_sqlite_write(session)
        query = query.with_for_update()
    rows = query.limit(2).all()
    if len(rows) > 1:
        raise SkillSetControlPlaneLockUnavailableError("Bot capability fence unavailable")
    return rows[0] if rows else None


def require_locked_capability_bot(
    session: Session, *, bot_id: str, entity_id: str,
) -> BotModel:
    bot = find_capability_bot(session, bot_id=bot_id, entity_id=entity_id, lock=True)
    if bot is None:
        raise SkillSetControlPlaneLockUnavailableError("Bot capability fence unavailable")
    return bot


def lock_bot_capabilities(
    session: Session, *, bot_id: str, entity_id: str, env: str | None = None,
) -> None:
    rows = (
        _capability_bots(session, bot_id=bot_id, entity_id=entity_id, env=env)
        .with_entities(BotModel.id)
        .with_for_update()
        .all()
    )
    if len(rows) != 1:
        raise SkillSetControlPlaneLockUnavailableError("Bot capability fence unavailable")


class SkillBatchTransactions:
    @contextmanager
    def skill_batch(self, *, bot_id: str, owner_id: str):
        """Commit a batch together while serializing other Bot capability writes."""
        if self._batch_session.get() is not None:
            # The enclosing projection mutation already owns the same Bot lock.
            yield
            return
        with self._db.transactional_orm_session() as session:
            _begin_sqlite_write(session)
            lock_bot_capabilities(session, bot_id=bot_id, entity_id=owner_id)
            token = self._batch_session.set(session)
            try:
                yield
            finally:
                self._batch_session.reset(token)

    @contextmanager
    def _skill_mutation_session(self):
        session = self._batch_session.get()
        if session is None:
            with self._db.transactional_orm_session() as session:
                yield session
        else:
            with session.begin_nested():
                yield session
