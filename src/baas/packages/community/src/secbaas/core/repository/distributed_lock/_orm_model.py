from sqlalchemy import BigInteger, Column, DateTime, String, UniqueConstraint, func

from secbaas.spi.database import Base

from ._record import LockRecord


class DistributedLockModel(Base):
    __tablename__ = "ac_lock_table"
    __table_args__ = (UniqueConstraint("lock_name", name="uk_lock_name"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    lock_name = Column(String(128), nullable=False)
    lock_holder = Column(String(128), nullable=False)
    expire_time = Column(DateTime, nullable=True)
    env = Column(String(64), nullable=True)

    def to_record(self) -> LockRecord:
        return LockRecord(
            id=self.id,
            lock_name=self.lock_name,
            lock_holder=self.lock_holder,
            expire_time=self.expire_time,
            env=self.env,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
        )
