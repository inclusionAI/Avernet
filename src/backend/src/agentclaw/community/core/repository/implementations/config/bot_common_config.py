"""ORM persistence shared by local SQLite and production MySQL."""

from collections.abc import Callable

from injector import inject
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.repository.protocols.config import (
    BotCommonConfigRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotCommonConfig


class BotCommonConfigRepository(BotCommonConfigRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str
    ) -> str | None:
        with self._db.orm_session() as session:
            row = (
                session.query(BotCommonConfig)
                .filter_by(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    env=env,
                    config_key=config_key,
                    is_delete=0,
                )
                .first()
            )
            return row.config_value if row else None

    def put(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        config_key: str,
        config_value: str,
    ) -> None:
        values = dict(
            bot_id=bot_id,
            entity_id=entity_id,
            env=env,
            config_key=config_key,
            config_value=config_value,
            is_delete=0,
        )
        with self._db.orm_session() as session:
            if session.get_bind().dialect.name == "mysql":
                from sqlalchemy.dialects.mysql import insert

                stmt = insert(BotCommonConfig).values(**values)
                stmt = stmt.on_duplicate_key_update(
                    config_value=config_value,
                    is_delete=0,
                    gmt_modified=func.now(),
                )
            else:
                from sqlalchemy.dialects.sqlite import insert

                stmt = insert(BotCommonConfig).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["bot_id", "entity_id", "env", "config_key"],
                    set_={
                        "config_value": config_value,
                        "is_delete": 0,
                        "gmt_modified": func.now(),
                    },
                )
            session.execute(stmt)

    def initialize_once(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str,
        config_key: str,
        factory: Callable[[], str],
    ) -> None:
        scope = dict(bot_id=bot_id, entity_id=entity_id, env=env, config_key=config_key)
        inserted = False
        try:
            with self._db.transactional_orm_session() as session:
                # The placeholder is never committed: claim the unique policy row
                # before evaluating rollout, then replace it in the same transaction.
                row = BotCommonConfig(**scope, config_value="{}")
                session.add(row)
                session.flush()
                inserted = True
                row.config_value = factory()
                session.flush()
        except IntegrityError:
            if inserted:
                raise
            with self._db.orm_session() as session:
                if session.query(BotCommonConfig).filter_by(**scope).first() is None:
                    raise
