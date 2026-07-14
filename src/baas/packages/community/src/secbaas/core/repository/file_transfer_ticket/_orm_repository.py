"""ORM implementation of TicketRepository using SQLAlchemy."""

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.core.utils.env_utils import get_current_env
from secbaas.logger import get_logger

from ._orm_model import FileTransferTicketModel
from ._protocol import (
    TicketRepository,
    TransferNotFoundError,
    TransferStateConflictError,
)
from ._record import TicketRecord

log = get_logger("orm-repository")

# Upload path: CREATED->UPLOADING->UPLOAD_COMPLETED->PULLING->DONE
# Retention path: CREATED->UPLOADING->UPLOAD_COMPLETED->DONE (device_path IS NULL)  [Phase 69]
# Download path: CREATED->PUSHING->DONE
# Cancel path: any non-terminal upload state -> CANCELLED (terminal)  [Phase 72]
# Delete staging path: DONE/FAILED/CANCELLED -> DELETED (terminal)  [Phase 72]
# Failure: any non-terminal state -> FAILED
# Same-state: idempotent no-op
VALID_TRANSITIONS = frozenset({
    ("CREATED", "UPLOADING"),
    ("UPLOADING", "UPLOAD_COMPLETED"),
    ("UPLOAD_COMPLETED", "PULLING"),
    ("UPLOAD_COMPLETED", "DONE"),  # Phase 69: retention mode shortcut (device_path IS NULL)
    ("PULLING", "DONE"),
    ("CREATED", "PUSHING"),
    ("PUSHING", "DONE"),
    # Failure from any non-terminal state
    ("CREATED", "FAILED"),
    ("UPLOADING", "FAILED"),
    ("UPLOAD_COMPLETED", "FAILED"),
    ("PULLING", "FAILED"),
    ("PUSHING", "FAILED"),
    # Phase 72: Cancel upload -- any non-terminal upload state -> CANCELLED
    ("CREATED", "CANCELLED"),
    ("UPLOADING", "CANCELLED"),
    ("UPLOAD_COMPLETED", "CANCELLED"),
    ("PULLING", "CANCELLED"),
    # Phase 72: Delete staging -- terminal states -> DELETED
    ("DONE", "DELETED"),
    ("FAILED", "DELETED"),
    ("CANCELLED", "DELETED"),
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
        multipart_session_id: str | None = None,
    ) -> int:
        log.info(
            "create_ticket: transfer_id=%s, direction=%s, multipart_session_id=%s",
            transfer_id, direction, multipart_session_id is not None,
        )
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
            multipart_session_id=multipart_session_id,
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
            "error_message": error_message,
        }
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
                raise TransferNotFoundError(transfer_id)
            else:
                raise TransferStateConflictError(
                    f"Cannot transition from {row.status} to {new_status}: "
                    "ticket is in a conflicting or terminal state."
                )
        log.info("[file-transfer:update_status] result: done")

    @with_orm_session
    def get_by_transfer_id(
        self, transfer_id: str, tenant: str | None = None,
    ) -> TicketRecord | None:
        log.info("get_by_transfer_id: transfer_id=%s, tenant=%s", transfer_id, tenant)
        env = get_current_env()
        filters = [
            FileTransferTicketModel.transfer_id == transfer_id,
            FileTransferTicketModel.env == env,
        ]
        if tenant is not None:
            filters.append(FileTransferTicketModel.tenant == tenant)
        row = (
            self._session.query(FileTransferTicketModel)
            .filter(*filters)
            .first()
        )
        if row is None:
            log.info("[file-transfer:get_by_transfer_id] result: not found")
            return None
        record = row.to_record()
        log.info("[file-transfer:get_by_transfer_id] result: id=%s", record.id)
        return record

    @with_orm_session
    def get_by_fileservice_staging_path(
        self, staging_path: str,
    ) -> TicketRecord | None:
        """Look up a ticket by its fileservice_staging_path.

        Staging path is globally unique per env (constructed from transfer_id),
        so no tenant filter is needed.

        Args:
            staging_path: Full OSS object key (fileservice_staging_path).

        Returns:
            TicketRecord if found, None otherwise.
        """
        log.info(
            "[file-transfer:get_by_staging_path] staging_path=%s", staging_path,
        )
        env = get_current_env()
        row = (
            self._session.query(FileTransferTicketModel)
            .filter(
                FileTransferTicketModel.fileservice_staging_path == staging_path,
                FileTransferTicketModel.env == env,
            )
            .first()
        )
        if row is None:
            log.info("[file-transfer:get_by_staging_path] result: not found")
            return None
        record = row.to_record()
        log.info("[file-transfer:get_by_staging_path] result: id=%s", record.id)
        return record

    @with_orm_session
    def update_urls(
        self,
        transfer_id: str,
        *,
        download_url: str | None = None,
        upload_url: str | None = None,
    ) -> None:
        log.info(
            "update_urls: transfer_id=%s, download_url=%s, upload_url=%s",
            transfer_id, bool(download_url), bool(upload_url),
        )
        from sqlalchemy import func

        env = get_current_env()
        update_kwargs: dict = {"gmt_modified": func.now()}
        if download_url is not None:
            update_kwargs["download_url"] = download_url
        if upload_url is not None:
            update_kwargs["upload_url"] = upload_url

        if "download_url" not in update_kwargs and "upload_url" not in update_kwargs:
            return  # no-op: both None

        result = (
            self._session.query(FileTransferTicketModel)
            .filter(
                FileTransferTicketModel.transfer_id == transfer_id,
                FileTransferTicketModel.env == env,
            )
            .update(update_kwargs, synchronize_session=False)
        )
        if result == 0:
            raise TransferNotFoundError(transfer_id)
        log.info("[file-transfer:update_urls] result: done")