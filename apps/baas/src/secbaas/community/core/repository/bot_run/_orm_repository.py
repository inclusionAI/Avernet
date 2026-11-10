import json
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import BotRunModel
from ._protocol import BotRunRepository
from ._record import (
    TERMINAL_STATUSES,
    BcnCallbackClaim,
    BcnCallbackState,
    BotRunRecord,
)

log = get_logger("orm")


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
        callback_function: str | None = None,
    ) -> str:
        log.info(
            "insert_run: run_id=%s, bot_id=%s, api_key_prefix=%s",
            run_id,
            bot_id,
            api_key_prefix,
        )
        bot_options = (metadata or {}).get("bot_options") or {}
        request_type = (metadata or {}).get("request_type")
        callback_eligible = (
            callback_function == "bcn_uplink"
            and bot_options.get("from_bcn") == "true"
            and request_type != "inject"
        )
        row = BotRunModel(
            run_id=run_id,
            bot_id=bot_id,
            api_key_prefix=api_key_prefix,
            message=message_long[:256] if message_long else "",
            message_long=message_long,
            metadata_=json.dumps(metadata, ensure_ascii=False) if metadata else None,
            status="PENDING",
            bcn_callback_function=callback_function,
            bcn_callback_eligible=callback_eligible,
            bcn_callback_state=(
                BcnCallbackState.PENDING.value if callback_eligible else None
            ),
            bcn_callback_sent=False,
            bcn_callback_attempts=0,
        )
        self._session.add(row)
        self._session.flush()
        log.info("[bot-run:insert_run] result: run_id=%s", run_id)
        return run_id

    @with_orm_session
    def claim_bcn_callback_attempt(
        self,
        run_id: str,
        *,
        now: datetime,
        lease_seconds: int,
        max_attempts: int,
    ) -> BcnCallbackClaim | None:
        """Claim one callback invocation using a durable, fenced lease."""
        if lease_seconds <= 0 or max_attempts <= 0:
            raise ValueError("lease_seconds and max_attempts must be positive")

        row = (
            self._session.query(BotRunModel)
            .filter(BotRunModel.run_id == run_id)
            .first()
        )
        if (
            row is None
            or not row.bcn_callback_eligible
            or row.bcn_callback_function != "bcn_uplink"
        ):
            return None
        if row.status not in TERMINAL_STATUSES:
            return None
        if row.bcn_callback_sent or row.bcn_callback_state in (
            BcnCallbackState.SENT.value,
            BcnCallbackState.TERMINAL_FAILED.value,
        ):
            return None

        state = row.bcn_callback_state
        due = state == BcnCallbackState.PENDING.value and (
            row.bcn_callback_next_retry_at is None
            or row.bcn_callback_next_retry_at <= now
        )
        stale = (
            state == BcnCallbackState.IN_FLIGHT.value
            and row.bcn_callback_in_flight_until is not None
            and row.bcn_callback_in_flight_until <= now
        )
        legacy_null = state is None
        if not (due or stale or legacy_null):
            return None

        attempts = int(row.bcn_callback_attempts or 0)
        if attempts >= max_attempts:
            self._session.query(BotRunModel).filter(
                BotRunModel.run_id == run_id,
                BotRunModel.bcn_callback_eligible.is_(True),
                BotRunModel.bcn_callback_function == "bcn_uplink",
                BotRunModel.bcn_callback_sent.is_(False),
                or_(
                    BotRunModel.bcn_callback_state.is_(None),
                    and_(
                        BotRunModel.bcn_callback_state
                        == BcnCallbackState.PENDING.value,
                        or_(
                            BotRunModel.bcn_callback_next_retry_at.is_(None),
                            BotRunModel.bcn_callback_next_retry_at <= now,
                        ),
                    ),
                    and_(
                        BotRunModel.bcn_callback_state
                        == BcnCallbackState.IN_FLIGHT.value,
                        BotRunModel.bcn_callback_in_flight_until <= now,
                    ),
                ),
            ).update(
                {
                    "bcn_callback_state": BcnCallbackState.TERMINAL_FAILED.value,
                    "bcn_callback_next_retry_at": None,
                    "bcn_callback_in_flight_until": None,
                    "bcn_callback_lease_token": None,
                    "bcn_callback_last_error_kind": "exhausted",
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
            return None

        lease_token = uuid4().hex
        generation = int(row.bcn_callback_lease_generation or 0) + 1
        lease_until = now + timedelta(seconds=lease_seconds)
        state_condition = or_(
            BotRunModel.bcn_callback_state.is_(None),
            and_(
                BotRunModel.bcn_callback_state == BcnCallbackState.PENDING.value,
                or_(
                    BotRunModel.bcn_callback_next_retry_at.is_(None),
                    BotRunModel.bcn_callback_next_retry_at <= now,
                ),
            ),
            and_(
                BotRunModel.bcn_callback_state == BcnCallbackState.IN_FLIGHT.value,
                BotRunModel.bcn_callback_in_flight_until <= now,
            ),
        )
        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.bcn_callback_eligible.is_(True),
                BotRunModel.bcn_callback_function == "bcn_uplink",
                BotRunModel.status.in_(tuple(TERMINAL_STATUSES)),
                BotRunModel.bcn_callback_sent.is_(False),
                state_condition,
            )
            .update(
                {
                    "bcn_callback_state": BcnCallbackState.IN_FLIGHT.value,
                    "bcn_callback_attempts": attempts + 1,
                    "bcn_callback_next_retry_at": None,
                    "bcn_callback_in_flight_until": lease_until,
                    "bcn_callback_lease_token": lease_token,
                    "bcn_callback_lease_generation": generation,
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            return None
        row = (
            self._session.query(BotRunModel).filter(BotRunModel.run_id == run_id).one()
        )
        return BcnCallbackClaim(
            record=row.to_record(),
            lease_token=lease_token,
            lease_generation=generation,
        )

    @with_orm_session
    def finish_bcn_callback_success(
        self,
        run_id: str,
        *,
        lease_token: str,
        lease_generation: int,
        status: str,
    ) -> bool:
        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.bcn_callback_state == BcnCallbackState.IN_FLIGHT.value,
                BotRunModel.bcn_callback_lease_token == lease_token,
                BotRunModel.bcn_callback_lease_generation == lease_generation,
            )
            .update(
                {
                    "bcn_callback_state": BcnCallbackState.SENT.value,
                    "bcn_callback_sent": True,
                    "bcn_callback_next_retry_at": None,
                    "bcn_callback_in_flight_until": None,
                    "bcn_callback_lease_token": None,
                    "bcn_callback_last_status": status,
                    "bcn_callback_last_error_kind": None,
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        return updated == 1

    @with_orm_session
    def finish_bcn_callback_retryable_failure(
        self,
        run_id: str,
        *,
        lease_token: str,
        lease_generation: int,
        next_retry_at: datetime,
        status: str,
        error_kind: str,
        max_attempts: int,
    ) -> bool:
        row = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.bcn_callback_state == BcnCallbackState.IN_FLIGHT.value,
                BotRunModel.bcn_callback_lease_token == lease_token,
                BotRunModel.bcn_callback_lease_generation == lease_generation,
            )
            .first()
        )
        if row is None:
            return False
        exhausted = int(row.bcn_callback_attempts or 0) >= max_attempts
        values = {
            "bcn_callback_state": (
                BcnCallbackState.TERMINAL_FAILED.value
                if exhausted
                else BcnCallbackState.PENDING.value
            ),
            "bcn_callback_next_retry_at": None if exhausted else next_retry_at,
            "bcn_callback_in_flight_until": None,
            "bcn_callback_lease_token": None,
            "bcn_callback_last_status": status,
            "bcn_callback_last_error_kind": "exhausted" if exhausted else error_kind,
            "gmt_modified": func.now(),
        }
        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.bcn_callback_state == BcnCallbackState.IN_FLIGHT.value,
                BotRunModel.bcn_callback_lease_token == lease_token,
                BotRunModel.bcn_callback_lease_generation == lease_generation,
            )
            .update(values, synchronize_session=False)
        )
        return updated == 1

    @with_orm_session
    def finish_bcn_callback_terminal_failure(
        self,
        run_id: str,
        *,
        lease_token: str,
        lease_generation: int,
        status: str,
        error_kind: str,
    ) -> bool:
        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.bcn_callback_state == BcnCallbackState.IN_FLIGHT.value,
                BotRunModel.bcn_callback_lease_token == lease_token,
                BotRunModel.bcn_callback_lease_generation == lease_generation,
            )
            .update(
                {
                    "bcn_callback_state": BcnCallbackState.TERMINAL_FAILED.value,
                    "bcn_callback_next_retry_at": None,
                    "bcn_callback_in_flight_until": None,
                    "bcn_callback_lease_token": None,
                    "bcn_callback_last_status": status,
                    "bcn_callback_last_error_kind": error_kind,
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        return updated == 1

    @with_orm_session
    def scan_bcn_callback_pending(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[BotRunRecord]:
        if limit <= 0:
            return []
        state_due = or_(
            BotRunModel.bcn_callback_state.is_(None),
            and_(
                BotRunModel.bcn_callback_state == BcnCallbackState.PENDING.value,
                or_(
                    BotRunModel.bcn_callback_next_retry_at.is_(None),
                    BotRunModel.bcn_callback_next_retry_at <= now,
                ),
            ),
            and_(
                BotRunModel.bcn_callback_state == BcnCallbackState.IN_FLIGHT.value,
                BotRunModel.bcn_callback_in_flight_until <= now,
            ),
        )
        rows = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.bcn_callback_eligible.is_(True),
                BotRunModel.bcn_callback_function == "bcn_uplink",
                BotRunModel.bcn_callback_sent.is_(False),
                BotRunModel.status.in_(tuple(TERMINAL_STATUSES)),
                state_due,
            )
            .order_by(BotRunModel.gmt_create.asc())
            .limit(limit)
            .all()
        )
        return [row.to_record() for row in rows]

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

        updated = (
            self._session.query(BotRunModel)
            .filter(
                BotRunModel.run_id == run_id,
                BotRunModel.status.in_(("PENDING", "RUNNING")),
            )
            .update(
                {
                    "status": "COMPLETED",
                    "result_content": content_long[:256] if content_long else "",
                    "result_content_long": content_long,
                    "result_extra": (
                        json.dumps(extra, ensure_ascii=False) if extra else None
                    ),
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
    def update_error(self, run_id: str, error: str) -> None:
        log.info("update_error: run_id=%s", run_id)
        from sqlalchemy import func

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
