"""ORM persistence shared by local SQLite and production MySQL."""

from injector import inject
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from agentclaw.community.core.repository.protocols.config import (
    BotCommonConfigRepositoryProtocol,
)
from agentclaw.community.core.common_config.models import BotCommonConfigRecord

from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotCommonConfig


class BotCommonConfigRepository(BotCommonConfigRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str
    ) -> str | None:
        try:
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
        except (OperationalError, ProgrammingError) as exc:
            # Only the not-yet-deployed table is equivalent to no policy.
            # Timeouts, permissions and invalid data must never downgrade UPFS.
            args = getattr(exc.orig, "args", ())
            if args and (
                args[0] == 1146 or str(args[0]) == "no such table: ac_bot_common_config"
            ):
                return None
            raise

    @staticmethod
    def _to_record(row: BotCommonConfig | None) -> BotCommonConfigRecord | None:
        if row is None:
            return None
        return BotCommonConfigRecord(
            id=row.id, bot_id=row.bot_id, entity_id=row.entity_id, env=row.env,
            config_key=row.config_key, config_value=row.config_value,
            is_delete=row.is_delete, gmt_create=row.gmt_create,
            gmt_modified=row.gmt_modified,
        )

    def get_record_by_id(self, *, config_id: int) -> BotCommonConfigRecord | None:
        with self._db.orm_session() as session:
            return self._to_record(
                session.query(BotCommonConfig).filter_by(id=config_id, is_delete=0).first()
            )

    def list_records(
        self, *, bot_id: str | None = None, entity_id: str | None = None,
        env: str | None = None, config_key: str | None = None,
        page_num: int = 1, page_size: int = 100,
    ) -> tuple[int, list[BotCommonConfigRecord]]:
        if page_num < 1 or page_size < 1 or page_size > 500:
            raise ValueError("page_num/page_size 参数不合法")
        with self._db.orm_session() as session:
            query = session.query(BotCommonConfig).filter_by(is_delete=0)
            for key, value in (("bot_id", bot_id), ("entity_id", entity_id),
                               ("env", env), ("config_key", config_key)):
                if value is not None:
                    query = query.filter(getattr(BotCommonConfig, key) == value)
            total = query.count()
            rows = query.order_by(BotCommonConfig.id.desc()).offset((page_num - 1) * page_size).limit(page_size).all()
            return total, [self._to_record(row) for row in rows]

    def create_record(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str, config_value: str
    ) -> int:
        with self._db.transactional_orm_session() as session:
            row = BotCommonConfig(bot_id=bot_id, entity_id=entity_id, env=env,
                                  config_key=config_key, config_value=config_value)
            session.add(row)
            session.flush()
            return row.id

    def update_record(self, *, config_id: int, config_value: str) -> bool:
        with self._db.orm_session() as session:
            row = session.query(BotCommonConfig).filter_by(id=config_id, is_delete=0).first()
            if row is None:
                return False
            row.config_value = config_value
            return True

    def delete_record(self, *, config_id: int) -> bool:
        with self._db.orm_session() as session:
            row = session.query(BotCommonConfig).filter_by(id=config_id, is_delete=0).first()
            if row is None:
                return False
            row.is_delete = 1
            return True

    def upsert_record(
        self, *, bot_id: str, entity_id: str, env: str, config_key: str, config_value: str
    ) -> int:
        with self._db.transactional_orm_session() as session:
            row = session.query(BotCommonConfig).filter_by(
                bot_id=bot_id, entity_id=entity_id, env=env, config_key=config_key
            ).first()
            if row is None:
                row = BotCommonConfig(bot_id=bot_id, entity_id=entity_id, env=env,
                                      config_key=config_key, config_value=config_value)
                session.add(row)
            else:
                row.config_value = config_value
                row.is_delete = 0
            session.flush()
            return row.id

    def batch_upsert_records(self, *, records: list[dict[str, str]]) -> list[int]:
        with self._db.transactional_orm_session() as session:
            ids = []
            for item in records:
                row = session.query(BotCommonConfig).filter_by(
                    bot_id=item["bot_id"], entity_id=item["entity_id"],
                    env=item["env"], config_key=item["config_key"]
                ).first()
                if row is None:
                    row = BotCommonConfig(**item)
                    session.add(row)
                else:
                    row.config_value = item["config_value"]
                    row.is_delete = 0
                session.flush()
                ids.append(row.id)
            return ids

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
        config_value: str,
    ) -> None:
        scope = dict(bot_id=bot_id, entity_id=entity_id, env=env, config_key=config_key)
        inserted = False
        try:
            with self._db.transactional_orm_session() as session:
                row = BotCommonConfig(**scope, config_value=config_value)
                session.add(row)
                session.flush()
                inserted = True
        except IntegrityError:
            if inserted:
                raise
            with self._db.orm_session() as session:
                if session.query(BotCommonConfig).filter_by(**scope).first() is None:
                    raise
