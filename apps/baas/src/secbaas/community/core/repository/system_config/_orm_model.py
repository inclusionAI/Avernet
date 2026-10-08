from sqlalchemy import BigInteger, Column, DateTime, String, Text, func

from secbaas.community.spi.database import Base

from ._record import SystemConfigRecord


class SystemConfigModel(Base):
    __tablename__ = "baas_system_config"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    conf_key = Column(String(128), nullable=False)
    conf_value = Column(Text, nullable=True)
    env = Column(String(32), nullable=False)
    name = Column(String(256), nullable=False)
    description = Column(String(512), nullable=True)
    creator = Column(String(128), nullable=False)
    modifier = Column(String(128), nullable=False)

    def to_record(self) -> SystemConfigRecord:
        return SystemConfigRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            conf_key=self.conf_key,
            conf_value=self.conf_value,
            env=self.env,
            name=self.name,
            description=self.description,
            creator=self.creator,
            modifier=self.modifier,
        )
