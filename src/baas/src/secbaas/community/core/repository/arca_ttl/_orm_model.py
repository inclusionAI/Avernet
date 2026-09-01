"""SQLAlchemy ORM model for baas_bot_ttl_renewal_schedule table."""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)

from secbaas.community.spi.database import Base

from ._record import TtlRenewalScheduleRecord


class TtlRenewalScheduleModel(Base):
    __tablename__ = "baas_bot_ttl_renewal_schedule"
    __table_args__ = (
        UniqueConstraint("env", "source_table", "source_id", name="uk_source"),
        Index("idx_schedule", "env", "status", "next_renew_at"),
        Index("idx_sandboxid", "sandbox_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    sandbox_id = Column(String(256), nullable=False)
    source_table = Column(String(32), nullable=False)
    source_id = Column(BigInteger, nullable=False)
    next_renew_at = Column(DateTime, nullable=False)
    renew_fail_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="ACTIVE")
    stop_reason = Column(String(64), nullable=True)
    last_renewed_at = Column(DateTime, nullable=True)
    # env last, mirroring the physical DDL column order (design doc §7.2).
    env = Column(String(32), nullable=False)

    def to_record(self) -> TtlRenewalScheduleRecord:
        return TtlRenewalScheduleRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            sandbox_id=self.sandbox_id,
            source_table=self.source_table,
            source_id=self.source_id,
            next_renew_at=self.next_renew_at,
            renew_fail_count=self.renew_fail_count or 0,
            status=self.status,
            stop_reason=self.stop_reason,
            last_renewed_at=self.last_renewed_at,
            env=self.env,
        )
