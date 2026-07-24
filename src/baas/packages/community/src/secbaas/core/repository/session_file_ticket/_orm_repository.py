"""ORM implementation of SessionTicketRepository using SQLAlchemy."""

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.core.utils.env_utils import get_current_env
from secbaas.logger import get_logger

from ._orm_model import SessionFileTicketModel
from ._protocol import (
    SessionTicketRepository,
    TransferNotFoundError,
    TransferStateConflictError,
)
from ._record import SessionTicketRecord

log = get_logger("orm-repository")

# Upload path:  CREATED -> UPLOADING -> DONE
# Cancel path:  CREATED/UPLOADING -> CANCELLED
# Failure path: UPLOADING -> FAILED
# Delete path:  DONE/FAILED/CANCELLED -> DELETED
# Same-state:   idempotent no-op
VALID_TRANSITIONS = frozenset(
    {
        # Upload path
        ("CREATED", "UPLOADING"),
        ("UPLOADING", "DONE"),
        # Cancel path
        ("CREATED", "CANCELLED"),
        ("UPLOADING", "CANCELLED"),
        # Failure path
        ("UPLOADING", "FAILED"),
        # Delete path (terminal states -> DELETED)
        ("DONE", "DELETED"),
        ("FAILED", "DELETED"),
        ("CANCELLED", "DELETED"),
    }
)


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
    def update_status(
        self,
        transfer_id: str,
        new_status: str,
        error_message: str | None = None,
    ) -> None:
        log.info(
            "update_status: transfer_id=%s, new_status=%s", transfer_id, new_status
        )
        from sqlalchemy import func

        env = get_current_env()
        # CAS-style atomic UPDATE: only modify the row if its current
        # status is one of the allowed source states for new_status
        # (plus new_status itself -- same-state is idempotent).
        allowed_current = {new_status} | {
            src for (src, dst) in VALID_TRANSITIONS if dst == new_status
        }
        update_kwargs = {
            "status": new_status,
            "gmt_modified": func.now(),
        }
        if error_message is not None:
            update_kwargs["error_message"] = error_message
        result = (
            self._session.query(SessionFileTicketModel)
            .filter(
                SessionFileTicketModel.transfer_id == transfer_id,
                SessionFileTicketModel.env == env,
                SessionFileTicketModel.status.in_(allowed_current),
            )
            .update(update_kwargs, synchronize_session=False)
        )
        if result == 0:
            # Either ticket not found or invalid transition -- distinguish
            # via a fallback query for the error message.
            row = (
                self._session.query(SessionFileTicketModel)
                .filter(
                    SessionFileTicketModel.transfer_id == transfer_id,
                    SessionFileTicketModel.env == env,
                )
                .first()
            )
            if row is None:
                raise TransferNotFoundError(transfer_id)
            else:
                raise TransferStateConflictError(
                    f"Cannot transition from {row.status} to {new_status}: "
                    "ticket is in a conflicting or terminal state."
                )
        log.info("[session-file:update_status] result: done")

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