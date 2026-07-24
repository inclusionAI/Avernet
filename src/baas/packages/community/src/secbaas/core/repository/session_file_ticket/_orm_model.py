"""SQLAlchemy ORM model for baas_session_file_tickets table."""

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

from secbaas.spi.database import Base

from ._record import SessionTicketRecord


class SessionFileTicketModel(Base):
    __tablename__ = "baas_session_file_tickets"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    transfer_id = Column(String(128), nullable=False)
    tenant = Column(String(128), nullable=False)
    session_id = Column(String(256), nullable=False)            # replaces paas_device_id
    # No direction column
    status = Column(String(32), nullable=False)
    staging_subdir = Column(String(1024), nullable=True)
    filename = Column(String(512), nullable=False)
    # No device_path column
    fileservice_staging_path = Column(String(1024), nullable=False)
    error_message = Column(Text, nullable=True)
    # No download_url column
    multipart_session_id = Column(String(256), nullable=True)
    env = Column(String(16), nullable=False)
    operator = Column(String(256), nullable=False, server_default="unknown")

    __table_args__ = (
        UniqueConstraint("transfer_id", name="uk_tid"),
        Index("idx_env_tnt_sid", "env", "tenant", "session_id"),
        Index("idx_env_tnt_dir", "env", "tenant", "staging_subdir"),
    )

    def to_record(self) -> SessionTicketRecord:
        return SessionTicketRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            transfer_id=self.transfer_id,
            tenant=self.tenant,
            session_id=self.session_id,
            status=self.status,
            staging_subdir=self.staging_subdir,
            filename=self.filename,
            fileservice_staging_path=self.fileservice_staging_path,
            error_message=self.error_message,
            multipart_session_id=self.multipart_session_id,
            env=self.env,
            operator=self.operator,
        )