from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    String,
    UniqueConstraint,
    func,
)

from secbaas.community.spi.database import Base

from ._record import ResourceKeyBotMappingRecord, ResourceKeyRecord


class ResourceKeyModel(Base):
    __tablename__ = "baas_resource_key"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    tenant = Column(String(128), nullable=False)
    resource_key = Column(String(128), nullable=False)
    app = Column(String(128), nullable=False)

    __table_args__ = (UniqueConstraint("resource_key", name="uk_resource_key"),)

    def to_record(self) -> ResourceKeyRecord:
        return ResourceKeyRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            tenant=self.tenant,
            resource_key=self.resource_key,
            app=self.app,
        )


class ResourceKeyBotMappingModel(Base):
    __tablename__ = "baas_resource_key_bot_mapping"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    resource_key_id = Column(BigInteger, nullable=False)
    bot_id = Column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("resource_key_id", "bot_id", name="uk_resource_key_id_bot_id"),
    )

    def to_record(self) -> ResourceKeyBotMappingRecord:
        return ResourceKeyBotMappingRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            resource_key_id=self.resource_key_id,
            bot_id=self.bot_id,
        )
