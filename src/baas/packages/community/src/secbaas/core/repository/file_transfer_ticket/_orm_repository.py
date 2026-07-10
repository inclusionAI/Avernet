"""ORM implementation of TicketRepository using SQLAlchemy."""

from secbaas.api.device_manage import DeviceCreationError
from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.core.utils.env_utils import get_current_env
from secbaas.logger import get_logger

from ._orm_model import FileTransferTicketModel
from ._protocol import TicketRepository
from ._record import TicketRecord

log = get_logger("orm-repository")

# Valid state transitions per CONTEXT.md status machine
# Upload path: CREATED->UPLOADING->UPLOAD_COMPLETED->PULLING->DONE
# Download path: CREATED->PUSHING->DONE
# Failure: any non-terminal state -> FAILED
# Same-state: idempotent no-op
VALID_TRANSITIONS = frozenset({
    ("CREATED", "UPLOADING"),
    ("UPLOADING", "UPLOAD_COMPLETED"),
    ("UPLOAD_COMPLETED", "PULLING"),
    ("PULLING", "DONE"),
    ("CREATED", "PUSHING"),
    ("PUSHING", "DONE"),
    # Failure from any non-terminal state
    ("CREATED", "FAILED"),
    ("UPLOADING", "FAILED"),
    ("UPLOAD_COMPLETED", "FAILED"),
    ("PULLING", "FAILED"),
    ("PUSHING", "FAILED"),
})


class OrmTicketRepository(OrmConnectionMixin, TicketRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def create_ticket(
        self,
        *,
        transfer_id: str,
        tenant: str,
        paas_device_id: str,
        direction: str,
        status: str,
        staging_subdir: str | None,
        filename: str,
        device_path: str | None,
        fileservice_staging_path: str,
        error_message: str | None,
    ) -> int:
        log.info("create_ticket: transfer_id=%s, direction=%s", transfer_id, direction)
        env = get_current_env()
        row = FileTransferTicketModel(
            transfer_id=transfer_id,
            tenant=tenant,
            paas_device_id=paas_device_id,
            direction=direction,
            status=status,
            staging_subdir=staging_subdir,
            filename=filename,
            device_path=device_path,
            fileservice_staging_path=fileservice_staging_path,
            error_message=error_message,
            env=env,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[file-transfer:create_ticket] result: id=%s", result)
        return result

    @with_orm_session
    def list_pending_uploads(
        self, statuses: list[str], limit: int
    ) -> list[TicketRecord]:
        log.info("list_pending_uploads: statuses=%s, limit=%s", statuses, limit)
        env = get_current_env()
        rows = (
            self._session.query(FileTransferTicketModel)
            .filter(
                FileTransferTicketModel.status.in_(statuses),
                FileTransferTicketModel.env == env,
            )
            .order_by(FileTransferTicketModel.gmt_create.asc())
            .limit(limit)
            .all()
        )
        records = [row.to_record() for row in rows]
        log.info("[file-transfer:list_pending_uploads] result: count=%s", len(records))
        return records

    @with_orm_session
    def update_status(
        self,
        transfer_id: str,
        new_status: str,
        error_message: str | None = None,
    ) -> None:
        log.info("update_status: transfer_id=%s, new_status=%s", transfer_id, new_status)
        from sqlalchemy import func

        env = get_current_env()
        # CAS-style atomic UPDATE: only modify the row if its current
        # status is one of the allowed source states for new_status
        # (plus new_status itself — same-state is idempotent).
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
            self._session.query(FileTransferTicketModel)
            .filter(
                FileTransferTicketModel.transfer_id == transfer_id,
                FileTransferTicketModel.env == env,
                FileTransferTicketModel.status.in_(allowed_current),
            )
            .update(update_kwargs, synchronize_session=False)
        )
        if result == 0:
            # Either ticket not found or invalid transition — distinguish
            # via a fallback query for the error message.
            row = (
                self._session.query(FileTransferTicketModel)
                .filter(
                    FileTransferTicketModel.transfer_id == transfer_id,
                    FileTransferTicketModel.env == env,
                )
                .first()
            )
            if row is None:
                raise DeviceCreationError(
                    error_code="FILE_TRANSFER_NOT_FOUND",
                    message=f"Transfer ticket {transfer_id} not found",
                )
            else:
                raise DeviceCreationError(
                    error_code="FILE_TRANSFER_STATE_CONFLICT",
                    message=(
                        f"Cannot transition from {row.status} to {new_status}: "
                        "ticket is in a conflicting or terminal state."
                    ),
                )
        log.info("[file-transfer:update_status] result: done")

    def _validate_transition(self, current: str, target: str) -> None:
        """Validate state transition. Raises DeviceCreationError on conflict.

        Same-state transitions are idempotent (no-op).
        """
        if current == target:
            return  # Idempotent: same status is a no-op

        if (current, target) in VALID_TRANSITIONS:
            return

        raise DeviceCreationError(
            error_code="FILE_TRANSFER_STATE_CONFLICT",
            message=(
                f"Invalid state transition: {current} -> {target} is not allowed."
            ),
        )