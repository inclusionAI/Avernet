from sqlalchemy import BigInteger, Column, DateTime, Integer, String, func

from secbaas.community.spi.database import Base

from ._record import BotDeviceRelRecord


class BotDeviceRelModel(Base):
    __tablename__ = "baas_bot_device_rel"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    tenant = Column(String(128), nullable=False)
    env = Column(String(32), nullable=False)
    domain = Column(String(128), nullable=False, default="")
    is_deleted = Column(BigInteger, nullable=False, default=0)
    creator = Column(String(128), nullable=False)
    modifier = Column(String(128), nullable=False)
    bot_id = Column(Integer, nullable=False)
    device_uuid = Column(String(64), nullable=False)

    def to_record(self) -> BotDeviceRelRecord:
        return BotDeviceRelRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            tenant=self.tenant,
            env=self.env,
            domain=self.domain,
            is_deleted=self.is_deleted or 0,
            creator=self.creator,
            modifier=self.modifier,
            bot_id=self.bot_id,
            device_uuid=self.device_uuid,
        )
