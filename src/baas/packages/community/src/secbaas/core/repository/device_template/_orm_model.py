from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, func

from secbaas.spi.database import Base

from ._record import DeviceTemplateRecord


class DeviceTemplateModel(Base):
    __tablename__ = "baas_device_template"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    gmt_create = Column(DateTime, nullable=False, server_default=func.now())
    gmt_modified = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    template_uuid = Column(String(64), nullable=False)
    tenant = Column(String(128), nullable=False)
    is_deleted = Column(BigInteger, nullable=False, default=0)
    creator = Column(String(128), nullable=False)
    modifier = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False)
    name = Column(String(256), nullable=False)
    description = Column(String(512), nullable=True)
    config = Column(Text, nullable=True)
    template_id = Column(Integer, nullable=False)
    type = Column(String(64), nullable=False)

    def to_record(self) -> DeviceTemplateRecord:
        import json

        try:
            cfg = (
                json.loads(self.config) if isinstance(self.config, str) else self.config
            )
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        return DeviceTemplateRecord(
            id=self.id,
            gmt_create=self.gmt_create,
            gmt_modified=self.gmt_modified,
            template_uuid=self.template_uuid,
            tenant=self.tenant,
            is_deleted=self.is_deleted or 0,
            creator=self.creator,
            modifier=self.modifier,
            status=self.status,
            name=self.name,
            description=self.description,
            config=cfg or {},
            template_id=self.template_id,
            type=self.type,
        )
