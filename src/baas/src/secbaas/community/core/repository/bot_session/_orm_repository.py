import json
from datetime import datetime
from typing import Any

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._orm_model import BotSessionModel
from ._protocol import BotSessionRepository
from ._record import BotSessionRecord

log = get_logger("orm-repository")


class OrmBotSessionRepository(OrmConnectionMixin, BotSessionRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_session(
        self,
        *,
        bot_uuid: str,
        invoker: str,
        session_id: str,
        req: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        err_msg: str | None = None,
        context: dict[str, Any] | None = None,
        status: str = "PENDING",
        device_uuid: str,
        tenant: str,
    ) -> int:
        log.info(
            "insert_session: bot_uuid=%s, session_id=%s, device_uuid=%s, tenant=%s",
            bot_uuid,
            session_id,
            device_uuid,
            tenant,
        )
        env = get_current_env()
        row = BotSessionModel(
            bot_uuid=bot_uuid,
            invoker=invoker,
            session_id=session_id,
            req=json.dumps(req, ensure_ascii=False) if req else None,
            result=json.dumps(result, ensure_ascii=False) if result else None,
            err_msg=err_msg,
            context=json.dumps(context, ensure_ascii=False) if context else None,
            status=status,
            device_uuid=device_uuid,
            env=env,
            tenant=tenant,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[bot-session:insert_session] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, session_pk_id: int) -> BotSessionRecord | None:
        log.info("get_by_id: session_pk_id=%s", session_pk_id)
        row = (
            self._session.query(BotSessionModel)
            .filter(BotSessionModel.id == session_pk_id)
            .first()
        )
        record = row.to_record() if row else None
        log.info("[bot-session:get_by_id] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def get_by_session_id(self, session_id: str) -> BotSessionRecord | None:
        log.info("get_by_session_id: session_id=%s", session_id)
        env = get_current_env()
        row = (
            self._session.query(BotSessionModel)
            .filter(
                BotSessionModel.session_id == session_id,
                BotSessionModel.env == env,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[bot-session:get_by_session_id] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def update_result(
        self,
        *,
        session_id: str,
        result: dict[str, Any] | None,
        err_msg: str | None = None,
        status: str,
    ) -> None:
        log.info("update_result: session_id=%s, status=%s", session_id, status)
        from sqlalchemy import func

        env = get_current_env()
        self._session.query(BotSessionModel).filter(
            BotSessionModel.session_id == session_id,
            BotSessionModel.env == env,
        ).update(
            {
                "result": json.dumps(result, ensure_ascii=False) if result else None,
                "err_msg": err_msg,
                "status": status,
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot-session:update_result] result: done")

    @with_orm_session
    def update_status(self, *, session_id: str, status: str) -> None:
        log.info("update_status: session_id=%s, status=%s", session_id, status)
        from sqlalchemy import func

        env = get_current_env()
        self._session.query(BotSessionModel).filter(
            BotSessionModel.session_id == session_id,
            BotSessionModel.env == env,
        ).update(
            {
                "status": status,
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot-session:update_status] result: done")

    @with_orm_session
    def update_context(
        self,
        *,
        session_id: str,
        context: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        err_msg: str | None = None,
    ) -> None:
        log.info("update_context: session_id=%s", session_id)
        from sqlalchemy import func

        env = get_current_env()

        set_values: dict[str, Any] = {"gmt_modified": func.now()}
        if result is not None:
            set_values["result"] = json.dumps(result, ensure_ascii=False)
        if err_msg is not None:
            set_values["err_msg"] = err_msg
        if context is not None:
            # Merge context in Python layer
            existing_row = (
                self._session.query(BotSessionModel.context)
                .filter(
                    BotSessionModel.session_id == session_id,
                    BotSessionModel.env == env,
                )
                .first()
            )
            existing_context = {}
            if existing_row and existing_row.context:
                try:
                    existing_context = (
                        json.loads(existing_row.context)
                        if isinstance(existing_row.context, str)
                        else existing_row.context
                    )
                except (json.JSONDecodeError, TypeError):
                    existing_context = {}
            merged = dict(existing_context) if existing_context else {}
            merged.update(context)
            set_values["context"] = json.dumps(merged, ensure_ascii=False)

        self._session.query(BotSessionModel).filter(
            BotSessionModel.session_id == session_id,
            BotSessionModel.env == env,
        ).update(set_values, synchronize_session=False)
        log.info("[bot-session:update_context] result: done")

    @with_orm_session
    def list_by_bot_uuid(
        self,
        *,
        bot_uuid: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[BotSessionRecord]]:
        log.info("list_by_bot_uuid: bot_uuid=%s, page=%s", bot_uuid, page)
        from sqlalchemy import func

        env = get_current_env()
        query = self._session.query(BotSessionModel).filter(
            BotSessionModel.bot_uuid == bot_uuid,
            BotSessionModel.env == env,
        )
        total = query.with_entities(func.count(BotSessionModel.id)).scalar()
        offset = (page - 1) * page_size
        rows = (
            query.order_by(BotSessionModel.gmt_create.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[bot-session:list_by_bot_uuid] result: %s rows", len(items))
        return total, items

    @with_orm_session
    def list_by_session_ids(self, session_ids: list[str]) -> list[BotSessionRecord]:
        log.info(
            "list_by_session_ids: session_ids_count=%s",
            len(session_ids) if session_ids else 0,
        )
        if not session_ids:
            items: list[BotSessionRecord] = []
            log.info("[bot-session:list_by_session_ids] result: 0 rows")
            return items
        env = get_current_env()
        rows = (
            self._session.query(BotSessionModel)
            .filter(
                BotSessionModel.session_id.in_(session_ids),
                BotSessionModel.env == env,
            )
            .order_by(BotSessionModel.gmt_create.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[bot-session:list_by_session_ids] result: %s rows", len(items))
        return items

    @with_orm_session
    def list_by_time_range(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        bot_uuid: str | None = None,
    ) -> list[BotSessionRecord]:
        log.info(
            "list_by_time_range: start_time=%s, end_time=%s, bot_uuid=%s",
            start_time,
            end_time,
            bot_uuid,
        )
        env = get_current_env()
        query = self._session.query(BotSessionModel).filter(
            BotSessionModel.gmt_create >= start_time,
            BotSessionModel.gmt_create <= end_time,
            BotSessionModel.env == env,
        )
        if bot_uuid is not None:
            query = query.filter(BotSessionModel.bot_uuid == bot_uuid)
        rows = query.order_by(BotSessionModel.gmt_create.desc()).all()
        items = [r.to_record() for r in rows]
        log.info("[bot-session:list_by_time_range] result: %s rows", len(items))
        return items

    @with_orm_session
    def list_by_bot_device_invoker(
        self,
        *,
        bot_uuid: str,
        device_uuid: str | None,
        invoker: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[BotSessionRecord]:
        log.info(
            "list_by_bot_device_invoker: bot_uuid=%s, device_uuid=%s, invoker=%s",
            bot_uuid,
            device_uuid,
            invoker,
        )
        env = get_current_env()
        query = self._session.query(BotSessionModel).filter(
            BotSessionModel.bot_uuid == bot_uuid,
            BotSessionModel.invoker == invoker,
            BotSessionModel.env == env,
            BotSessionModel.gmt_create >= start_time,
            BotSessionModel.gmt_create <= end_time,
        )
        if device_uuid is not None:
            query = query.filter(BotSessionModel.device_uuid == device_uuid)
        rows = query.order_by(BotSessionModel.gmt_create.desc()).all()
        items = [r.to_record() for r in rows]
        log.info("[bot-session:list_by_bot_device_invoker] result: %s rows", len(items))
        return items

    @with_orm_session
    def count_active_sessions_by_device(self, *, device_uuid: str, tenant: str) -> int:
        log.info(
            "count_active_sessions_by_device: device_uuid=%s, tenant=%s",
            device_uuid,
            tenant,
        )
        from sqlalchemy import func

        env = get_current_env()
        count = (
            self._session.query(func.count(BotSessionModel.id))
            .filter(
                BotSessionModel.device_uuid == device_uuid,
                BotSessionModel.env == env,
                BotSessionModel.tenant == tenant,
                BotSessionModel.status.in_(["PENDING", "RUNNING"]),
            )
            .scalar()
        )
        result = count or 0
        log.info("[bot-session:count_active_sessions_by_device] result: %s", result)
        return result
