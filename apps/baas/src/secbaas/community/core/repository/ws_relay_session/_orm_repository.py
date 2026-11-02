import json
from datetime import UTC, datetime
from typing import Any

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._orm_model import WsRelaySessionModel
from ._protocol import WsRelaySessionRepository
from ._record import WsRelaySessionRecord

log = get_logger("orm")


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
        expected_statuses: frozenset[str],
    ) -> int:
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
                WsRelaySessionModel.status.in_(expected_statuses),
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
        return int(result)

    @with_orm_session
    def update_closed(
        self, *, session_id: str, expected_statuses: frozenset[str] | None = None
    ) -> int:
        log.info("update_closed: session_id=%s", session_id)
        from sqlalchemy import func

        env = get_current_env()
        query = self._session.query(WsRelaySessionModel).filter(
            WsRelaySessionModel.session_id == session_id,
            WsRelaySessionModel.env == env,
        )
        if expected_statuses is not None:
            query = query.filter(WsRelaySessionModel.status.in_(expected_statuses))
        result = query.update(
            {
                "status": "closed",
                "gmt_close": datetime.now(UTC),
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        return int(result)
