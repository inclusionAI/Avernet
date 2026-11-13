"""Serialize one persisted Bot's capability mutations in their DB transaction."""

from contextlib import contextmanager

from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneLockUnavailableError,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def lock_bot_capabilities(session, *, bot_id: str, entity_id: str, env: str | None = None) -> None:
    rows = (
        session.query(BotModel.id)
        .filter(
            BotModel.avernet_tenant == get_current_avernet_tenant(),
            BotModel.env == (env or get_current_env()),
            BotModel.bot_id == bot_id,
            BotModel.entity_id == entity_id,
            BotModel.is_delete == 0,
        )
        .with_for_update()
        .all()
    )
    if len(rows) != 1:
        raise SkillSetControlPlaneLockUnavailableError("Bot capability fence unavailable")


class SkillBatchTransactions:
    @contextmanager
    def skill_batch(self, *, bot_id: str, owner_id: str):
        """Commit a batch together while serializing other Bot capability writes."""
        with self._db.transactional_orm_session() as session:
            if session.get_bind().dialect.name == "sqlite":
                # A released SAVEPOINT must remain inside the outer transaction.
                connection = session.connection()
                if not connection.connection.driver_connection.in_transaction:
                    connection.exec_driver_sql("BEGIN")
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
