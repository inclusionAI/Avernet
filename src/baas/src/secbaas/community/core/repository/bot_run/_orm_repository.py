import json
from typing import Any

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import BotRunModel
from ._protocol import BotRunRepository
from ._record import BotRunRecord

log = get_logger("orm-repository")


class OrmBotRunRepository(OrmConnectionMixin, BotRunRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_run(
        self,
        *,
        run_id: str,
        bot_id: str,
        api_key_prefix: str,
        message_long: str,
        metadata: dict[str, Any] | None,
    ) -> str:
        log.info(
            "insert_run: run_id=%s, bot_id=%s, api_key_prefix=%s",
            run_id,
            bot_id,
            api_key_prefix,
        )
        row = BotRunModel(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix=api_key_prefix,
            message=message_long[:256] if message_long else "",
            message_long=message_long,
            metadata_=json.dumps(metadata, ensure_ascii=False) if metadata else None,
            status="PENDING",
        )
        self._session.add(row)
        self._session.flush()
        log.info("[bot-run:insert_run] result: run_id=%s", run_id)
        return run_id

    @with_orm_session
    def get_by_run_id(self, run_id: str) -> BotRunRecord | None:
        log.info("get_by_run_id: run_id=%s", run_id)
        row = (
            self._session.query(BotRunModel)
            .filter(BotRunModel.run_id == run_id)
            .first()
        )
        record = row.to_record() if row else None
        log.info("[bot-run:get_by_run_id] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def update_status(self, run_id: str, status: str) -> None:
        log.info("update_status: run_id=%s, status=%s", run_id, status)
        from sqlalchemy import func

        self._session.query(BotRunModel).filter(BotRunModel.run_id == run_id).update(
            {"status": status, "gmt_modified": func.now()}, synchronize_session=False
        )
        log.info("[bot-run:update_status] result: done")

    @with_orm_session
    def update_result(
        self,
        run_id: str,
        content_long: str,
        extra: dict[str, Any] | None,
    ) -> None:
        log.info("update_result: run_id=%s", run_id)
        from sqlalchemy import func

        self._session.query(BotRunModel).filter(BotRunModel.run_id == run_id).update(
            {
                "status": "COMPLETED",
                "result_content": content_long[:256] if content_long else "",
                "result_content_long": content_long,
                "result_extra": json.dumps(extra, ensure_ascii=False)
                if extra
                else None,
                "completed_at": func.now(),
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot-run:update_result] result: done")

    @with_orm_session
    def update_error(self, run_id: str, error: str) -> None:
        log.info("update_error: run_id=%s", run_id)
        from sqlalchemy import func

        self._session.query(BotRunModel).filter(BotRunModel.run_id == run_id).update(
            {
                "status": "FAILED",
                "error": error,
                "completed_at": func.now(),
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot-run:update_error] result: done")

    @with_orm_session
    def update_session_id(self, run_id: str, session_id: str) -> None:
        log.info("update_session_id: run_id=%s, session_id=%s", run_id, session_id)
        from sqlalchemy import func

        row = (
            self._session.query(BotRunModel)
            .filter(BotRunModel.run_id == run_id)
            .first()
        )
        if row is None:
            log.warning("update_session_id: run_id=%s not found", run_id)
            return

        # Merge session_id into result_extra JSON
        existing_extra: dict[str, Any] = {}
        if row.result_extra:
            try:
                existing_extra = json.loads(row.result_extra)
            except (json.JSONDecodeError, TypeError):
                existing_extra = {}
        existing_extra["session_id"] = session_id

        self._session.query(BotRunModel).filter(BotRunModel.run_id == run_id).update(
            {
                "result_extra": json.dumps(existing_extra, ensure_ascii=False),
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot-run:update_session_id] result: done")
