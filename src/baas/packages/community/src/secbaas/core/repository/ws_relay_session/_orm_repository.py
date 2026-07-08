import json
from datetime import UTC, datetime
from typing import Any

from secbaas.api.device_manage import DeviceCreationError
from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.core.utils.env_utils import get_current_env
from secbaas.logger import get_logger

from ._orm_model import WsRelaySessionModel
from ._protocol import WsRelaySessionRepository
from ._record import WsRelaySessionRecord

log = get_logger("orm-repository")

# Valid state transitions for _validate_transition
# Same-status transitions are idempotent (no-op)
# Reverse transitions (closed -> active) are illegal
VALID_TRANSITIONS = frozenset(
    {
        ("init", "active"),
        ("init", "closed"),
        ("active", "closed"),
    }
)


class OrmWsRelaySessionRepository(OrmConnectionMixin, WsRelaySessionRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_init(
        self,
        *,
        session_id: str,
        machine_id: str,
        operator: str,
    ) -> int:
        log.info(
            "insert_init: session_id=%s, machine_id=%s, operator=%s",
            session_id,
            machine_id,
            operator,
        )
        env = get_current_env()
        row = WsRelaySessionModel(
            session_id=session_id,
            machine_id=machine_id,
            connected_server_instance="",  # NOT NULL placeholder (D-01)
            status="init",
            env=env,
            gmt_close=None,
            connected_route_info="{}",  # NOT NULL placeholder (D-01)
            operator=operator,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[ws-relay-session:insert_init] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_session_id(self, session_id: str) -> WsRelaySessionRecord | None:
        log.info("get_by_session_id: session_id=%s", session_id)
        env = get_current_env()
        row = (
            self._session.query(WsRelaySessionModel)
            .filter(
                WsRelaySessionModel.session_id == session_id,
                WsRelaySessionModel.env == env,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[ws-relay-session:get_by_session_id] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def update_active(
        self,
        *,
        session_id: str,
        connected_server_instance: str,
        connected_route_info: dict[str, Any],
    ) -> None:
        log.info(
            "update_active: session_id=%s, server_instance=%s",
            session_id,
            connected_server_instance,
        )
        from sqlalchemy import func

        env = get_current_env()
        result = (
            self._session.query(WsRelaySessionModel)
            .filter(
                WsRelaySessionModel.session_id == session_id,
                WsRelaySessionModel.env == env,
            )
            .update(
                {
                    "status": "active",
                    "connected_server_instance": connected_server_instance,
                    "connected_route_info": json.dumps(
                        connected_route_info, ensure_ascii=False
                    ),
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        if result == 0:
            raise DeviceCreationError(
                error_code="RELAY_SESSION_NOT_FOUND",
                message=(
                    f"Relay session {session_id} not found "
                    f"(may have been deleted concurrently)"
                ),
            )
        log.info("[ws-relay-session:update_active] result: done")

    @with_orm_session
    def update_closed(self, *, session_id: str) -> None:
        log.info("update_closed: session_id=%s", session_id)
        from sqlalchemy import func

        env = get_current_env()
        result = (
            self._session.query(WsRelaySessionModel)
            .filter(
                WsRelaySessionModel.session_id == session_id,
                WsRelaySessionModel.env == env,
            )
            .update(
                {
                    "status": "closed",
                    "gmt_close": datetime.now(UTC),
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        if result == 0:
            raise DeviceCreationError(
                error_code="RELAY_SESSION_NOT_FOUND",
                message=(
                    f"Relay session {session_id} not found "
                    f"(may have been deleted concurrently)"
                ),
            )
        log.info("[ws-relay-session:update_closed] result: done")

    def _validate_transition(self, current: str, target: str) -> None:
        """Validate state transition. Raises DeviceCreationError on conflict.

        Valid transitions:
            - init -> active
            - init -> closed
            - active -> closed
            - same -> same (idempotent)

        Raises:
            DeviceCreationError: with error_code="RELAY_STATE_CONFLICT" for
                illegal reverse transitions (e.g., closed -> active).
        """
        if current == target:
            return  # Idempotent: same status is a no-op

        if (current, target) in VALID_TRANSITIONS:
            return

        raise DeviceCreationError(
            error_code="RELAY_STATE_CONFLICT",
            message=(
                f"Invalid state transition: {current} -> {target} is not allowed. "
                f"Closed sessions cannot be reopened."
            ),
        )
