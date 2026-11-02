"""ORM implementation of TicketRepository using SQLAlchemy."""

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._orm_model import FileTransferTicketModel
from ._protocol import (
    TicketRepository,
    TransferNotFoundError,
)
from ._record import TicketRecord

log = get_logger("orm")


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
        operator: str = "unknown",
    ) -> int:
        log.info(
            "create_ticket: transfer_id=%s, direction=%s, multipart_session_id=%s",
            transfer_id,
            direction,
            multipart_session_id is not None,
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
            operator=operator,
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
            self._session.query(FileTransferTicketModel)
            .filter(
                FileTransferTicketModel.transfer_id == transfer_id,
                FileTransferTicketModel.env == get_current_env(),
                FileTransferTicketModel.status.in_(expected_statuses),
            )
            .update(update_kwargs, synchronize_session=False)
        )

    @with_orm_session
    def get_by_transfer_id(
        self,
        transfer_id: str,
        tenant: str | None = None,
    ) -> TicketRecord | None:
        log.info("get_by_transfer_id: transfer_id=%s, tenant=%s", transfer_id, tenant)
        env = get_current_env()
        filters = [
            FileTransferTicketModel.transfer_id == transfer_id,
            FileTransferTicketModel.env == env,
        ]
        if tenant is not None:
            filters.append(FileTransferTicketModel.tenant == tenant)
        row = self._session.query(FileTransferTicketModel).filter(*filters).first()
        if row is None:
            log.info("[file-transfer:get_by_transfer_id] result: not found")
            return None
        record = row.to_record()
        log.info("[file-transfer:get_by_transfer_id] result: id=%s", record.id)
        return record

    @with_orm_session
    def update_urls(
        self,
        transfer_id: str,
        *,
        download_url: str | None = None,
    ) -> None:
        download_url_preview = (download_url[:200]) if download_url else None
        log.info(
            "update_urls: transfer_id=%s, download_url=%s, download_url_preview=%s",
            transfer_id,
            bool(download_url),
            download_url_preview,
        )
        from sqlalchemy import func

        env = get_current_env()
        update_kwargs: dict = {"gmt_modified": func.now()}
        if download_url is not None:
            update_kwargs["download_url"] = download_url
        else:
            log.debug(
                "update_urls: transfer_id=%s, download_url is None, no-op", transfer_id
            )
            return  # no-op

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
