"""SQLAlchemy ORM model for baas_file_transfer_tickets table."""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)

from secbaas.community.spi.database import Base

from ._record import TicketRecord


class FileTransferTicketModel(Base):
    __tablename__ = "baas_file_transfer_tickets"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    transfer_id = Column(String(128), nullable=False)
    tenant = Column(String(128), nullable=False)
    paas_device_id = Column(String(512), nullable=False)
    direction = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False)
    staging_subdir = Column(String(1024), nullable=True)
    filename = Column(String(512), nullable=False)
    device_path = Column(String(1024), nullable=True)
    fileservice_staging_path = Column(String(1024), nullable=False)
    error_message = Column(Text, nullable=True)
    download_url = Column(String(2048), nullable=True)
    upload_url = Column(String(2048), nullable=True)
    multipart_session_id = Column(String(256), nullable=True)
    env = Column(String(16), nullable=False)

    __table_args__ = (
        UniqueConstraint("transfer_id", "env", name="uk_ft_transfer_id_env"),
        Index("idx_ft_status_created", "status", "gmt_create"),
    )

    def to_record(self) -> TicketRecord:
        return TicketRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            transfer_id=self.transfer_id,
            tenant=self.tenant,
            paas_device_id=self.paas_device_id,
            direction=self.direction,
            status=self.status,
            staging_subdir=self.staging_subdir,
            filename=self.filename,
            device_path=self.device_path,
            fileservice_staging_path=self.fileservice_staging_path,
            error_message=self.error_message,
            download_url=self.download_url,
            upload_url=self.upload_url,
            multipart_session_id=self.multipart_session_id,
            env=self.env,
        )
