"""ORM implementation of SessionTicketRepository using SQLAlchemy."""

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._orm_model import SessionFileTicketModel
from ._protocol import (
    SessionTicketRepository,
)
from ._record import SessionTicketRecord

log = get_logger("orm")


class OrmSessionTicketRepository(OrmConnectionMixin, SessionTicketRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def create_ticket(
        self,
        *,
        transfer_id: str,
        tenant: str,
        session_id: str,
        status: str,
        staging_subdir: str | None,
        filename: str,
        fileservice_staging_path: str,
        error_message: str | None,
        multipart_session_id: str | None = None,
        operator: str = "unknown",
    ) -> int:
        log.info(
            "create_ticket: transfer_id=%s, session_id=%s",
            transfer_id,
            session_id,
        )
        env = get_current_env()
        row = SessionFileTicketModel(
            transfer_id=transfer_id,
            tenant=tenant,
            session_id=session_id,
            status=status,
            staging_subdir=staging_subdir,
            filename=filename,
            fileservice_staging_path=fileservice_staging_path,
            error_message=error_message,
            multipart_session_id=multipart_session_id,
            env=env,
            operator=operator,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[session-file:create_ticket] result: id=%s", result)
        return result

    @with_orm_session
    def list_by_session(
        self, tenant: str, session_id: str
    ) -> list[SessionTicketRecord]:
        log.info("list_by_session: tenant=%s, session_id=%s", tenant, session_id)
        env = get_current_env()
        rows = (
            self._session.query(SessionFileTicketModel)
            .filter(
                SessionFileTicketModel.tenant == tenant,
                SessionFileTicketModel.session_id == session_id,
                SessionFileTicketModel.env == env,
            )
            .order_by(SessionFileTicketModel.gmt_create.desc())
            .all()
        )
        records = [row.to_record() for row in rows]
        log.info("[session-file:list_by_session] result: count=%s", len(records))
        return records

    @with_orm_session
    def compare_and_set_status(
        self,
        transfer_id: str,
        new_status: str,
        error_message: str | None = None,
        *,
        expected_statuses: frozenset[str],
    ) -> int:
        """One conditional write; transition policy belongs to the service.

        Return the driver's affected-row count. No fallback SELECT is executed
        while this UPDATE's connection/locks are held.
        """
        from sqlalchemy import func

        update_kwargs = {"status": new_status, "gmt_modified": func.now()}
        if error_message is not None:
            update_kwargs["error_message"] = error_message
        return (
            self._session.query(SessionFileTicketModel)
            .filter(
                SessionFileTicketModel.transfer_id == transfer_id,
                SessionFileTicketModel.env == get_current_env(),
                SessionFileTicketModel.status.in_(expected_statuses),
            )
            .update(update_kwargs, synchronize_session=False)
        )

    @with_orm_session
    def get_by_transfer_id(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> SessionTicketRecord | None:
        log.info("get_by_transfer_id: transfer_id=%s, tenant=%s", transfer_id, tenant)
        env = get_current_env()
        filters = [
            SessionFileTicketModel.transfer_id == transfer_id,
            SessionFileTicketModel.env == env,
        ]
        if tenant is not None:
            filters.append(SessionFileTicketModel.tenant == tenant)
        row = self._session.query(SessionFileTicketModel).filter(*filters).first()
        if row is None:
            log.info("[session-file:get_by_transfer_id] result: not found")
            return None
        record = row.to_record()
        log.info("[session-file:get_by_transfer_id] result: id=%s", record.id)
        return record
