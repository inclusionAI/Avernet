"""ORM-backed AC Bots repository implementation.

Read-only access to ac_bots table (maintained externally by AgentClaw).
Uses @with_orm_session decorator for SQLAlchemy ORM Session lifecycle.
"""

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

from ._orm_model import AcBotModel
from ._protocol import AcBotRepository
from ._record import AcBotRecord

log = get_logger("orm-repository")


class OrmAcBotRepository(OrmConnectionMixin, AcBotRepository):
    """ORM-based read-only access to ac_bots table."""

    def __init__(self, database) -> None:
        self._database = database

    @staticmethod
    def _model_to_record(row: AcBotModel | None) -> AcBotRecord | None:
        if row is None:
            return None
        import json

        def _json_load(v):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    return None
            return v

        return AcBotRecord(
            id=row.id,
            bot_id=row.bot_id,
            bot_name=row.bot_name,
            bot_desc=row.bot_desc,
            entity_id=row.entity_id,
            entity_type=row.entity_type or "",
            creator_id=row.creator_id or "",
            owner_id=row.owner_id or "",
            engine_types=_json_load(row.engine_types),
            status=row.status,
            binding_id=row.binding_id,
            gmt_create=row.gmt_create,
            gmt_modified=row.gmt_modified,
            modifier_id=row.modifier_id,
            share_policy=_json_load(row.share_policy),
            is_delete=row.is_delete,
            active_engine=row.active_engine,
            device_id=row.device_id,
            env=row.env,
            owner_name=row.owner_name,
            public=row.public or "",
            ext=_json_load(row.ext),
            bot_type=row.bot_type,
        )

    @with_orm_session
    def get_by_entity_id_bot_id_env(
        self,
        *,
        entity_id: str,
        bot_id: str,
        env: str,
    ) -> AcBotRecord | None:
        log.info(
            "get_by_entity_id_bot_id_env: entity_id=%s, bot_id=%s, env=%s",
            entity_id,
            bot_id,
            env,
        )
        """Query bot by entity_id, bot_id, and env."""
        row = (
            self._session.query(AcBotModel)
            .filter(
                AcBotModel.entity_id == entity_id,
                AcBotModel.bot_id == bot_id,
                AcBotModel.env == env,
                AcBotModel.is_delete == 0,
            )
            .first()
        )
        result = self._model_to_record(row)
        log.info(
            "[ac-bot:get_by_entity_id_bot_id_env] result: %s",
            result.id if result else "None",
        )
        return result

    @with_orm_session
    def get_active_by_entity_id_bot_id_env(
        self,
        *,
        entity_id: str,
        bot_id: str,
        env: str,
    ) -> AcBotRecord | None:
        """Query ACTIVE bot by entity_id, bot_id, and env.

        Like get_by_entity_id_bot_id_env but also filters status='ACTIVE'.
        Matches the 0525 SQL: WHERE is_delete=0 AND status='ACTIVE'.
        """
        log.info(
            "get_active_by_entity_id_bot_id_env: entity_id=%s, bot_id=%s, env=%s",
            entity_id,
            bot_id,
            env,
        )
        row = (
            self._session.query(AcBotModel)
            .filter(
                AcBotModel.entity_id == entity_id,
                AcBotModel.bot_id == bot_id,
                AcBotModel.env == env,
                AcBotModel.is_delete == 0,
                AcBotModel.status == "ACTIVE",
            )
            .first()
        )
        result = self._model_to_record(row)
        log.info(
            "[ac-bot:get_active_by_entity_id_bot_id_env] result: %s",
            result.id if result else "None",
        )
        return result

    @with_orm_session
    def get_by_bot_id_env_exclude_default(
        self,
        *,
        bot_id: str,
        env: str,
    ) -> AcBotRecord | None:
        log.info("get_by_bot_id_env_exclude_default: bot_id=%s, env=%s", bot_id, env)
        """Query bot by bot_id and env, excluding 'default'.

        Raises:
            ValueError: When bot_id is 'default'.
        """
        if bot_id == "default":
            raise ValueError("bot_id cannot be 'default'")

        row = (
            self._session.query(AcBotModel)
            .filter(
                AcBotModel.bot_id == bot_id,
                AcBotModel.env == env,
                AcBotModel.is_delete == 0,
            )
            .first()
        )
        result = self._model_to_record(row)
        log.info(
            "[ac-bot:get_by_bot_id_env_exclude_default] result: %s",
            result.id if result else "None",
        )
        return result

    @with_orm_session
    def list_active_bots(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str = "prod",
        bot_type: str | None = None,
    ) -> tuple[int, list[AcBotRecord]]:
        from sqlalchemy import func

        q = self._session.query(AcBotModel).filter(
            AcBotModel.is_delete == 0,
            AcBotModel.status == "ACTIVE",
            AcBotModel.env == env,
        )
        if bot_type is not None:
            q = q.filter(AcBotModel.bot_type == bot_type)

        total = q.with_entities(func.count(AcBotModel.id)).scalar() or 0
        offset = (page - 1) * page_size
        rows = q.order_by(AcBotModel.id.desc()).offset(offset).limit(page_size).all()
        items = [self._model_to_record(r) for r in rows]
        log.info(
            "[ac-bot:list_active_bots] returned %s items, total=%s",
            len(items),
            total,
        )
        return total, items
