import json
from typing import Any

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.repository.bot_run_queue import active_queue_claim_exists
from secbaas.community.logger import get_logger

from ._orm_model import BotRunModel
from ._protocol import BotRunRepository
from ._record import BotRunRecord

log = get_logger("orm")
_ERROR_CODE_MAX_BYTES = 65_535  # MySQL/MariaDB TEXT capacity.


def _storable_error_code(code: str | None) -> str | None:
    # 以下为安全注释COSEC：拒绝无法存储的原码，避免影响失败状态落库；不截断或记录原码。
    if (
        not isinstance(code, str)
        or len(code) > _ERROR_CODE_MAX_BYTES
        or not code.strip()
    ):
        return None
    try:
        return code if len(code.encode("utf-8")) <= _ERROR_CODE_MAX_BYTES else None
    except UnicodeEncodeError:
        return None


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
            {"status": status, "error_code": None, "gmt_modified": func.now()},
            synchronize_session=False,
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

        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.status.in_(("PENDING", "RUNNING")),
            )
            .update(
                {
                    "status": "COMPLETED",
                    "error_code": None,
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
        )
        if updated == 0:
            log.warning(
                "[bot-run:update_result] skipped (already terminal) run_id=%s", run_id
            )
        else:
            log.info("[bot-run:update_result] result: done")

    @with_orm_session
    def update_error(
        self,
        run_id: str,
        error: str,
        *,
        error_code: str | None = None,
        queue_claim: tuple[str, str] | None = None,
    ) -> None:
        log.info("update_error: run_id=%s", run_id)
        from sqlalchemy import ColumnElement, case, func

        code = _storable_error_code(error_code)
        persisted_code: str | None | ColumnElement[str] = code
        if code is not None and queue_claim is not None:
            # COSEC: verify queue-attempt ownership inside this update (not a
            # snapshot in the executor); only the new code is gated.
            claim_id, worker_id = queue_claim
            owns_claim = active_queue_claim_exists(
                run_id=run_id,
                claim_id=claim_id,
                worker_id=worker_id,
            )
            persisted_code = case((owns_claim, code), else_=None)

        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.status.in_(("PENDING", "RUNNING")),
            )
            .update(
                {
                    "status": "FAILED",
                    "error": error,
                    "error_code": persisted_code,
                    "completed_at": func.now(),
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        if updated == 0:
            log.warning(
                "[bot-run:update_error] skipped (already terminal) run_id=%s", run_id
            )
        else:
            log.info("[bot-run:update_error] result: done")

    @with_orm_session
    def update_aborted(self, run_id: str, reason: str) -> None:
        log.info("update_aborted: run_id=%s", run_id)
        from sqlalchemy import func

        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.status.in_(("PENDING", "RUNNING")),
            )
            .update(
                {
                    "status": "ABORTED",
                    "error_code": None,
                    "error": reason,
                    "completed_at": func.now(),
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        if updated == 0:
            log.warning(
                "[bot-run:update_aborted] skipped (already terminal) run_id=%s", run_id
            )
        else:
            log.info("[bot-run:update_aborted] result: done")

    @with_orm_session
    def update_timeout(self, run_id: str, error: str) -> None:
        log.info("update_timeout: run_id=%s", run_id)
        from sqlalchemy import func

        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.status.in_(("PENDING", "RUNNING")),
            )
            .update(
                {
                    "status": "TIME_OUT",
                    "error_code": None,
                    "error": error,
                    "completed_at": func.now(),
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        if updated == 0:
            log.warning(
                "[bot-run:update_timeout] skipped (already terminal) run_id=%s", run_id
            )
        else:
            log.info("[bot-run:update_timeout] result: done")

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
